import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from research_minute_market_pilot import initialize
from research_selection_shadow import compare_cycle,metrics,report

class SelectionShadowTests(unittest.TestCase):
    def test_metrics_do_not_invent_liquidity(self):
        rows=[("a","pa","t"),("b","pb","t"),("c","pc","t")]
        found={
            "a":{"pair":"pa","liquidity":None,"volume":300,"buys":5,"sells":1},
            "b":{"pair":"pb","liquidity":25000,"volume":300,"buys":5,"sells":1},
            "c":{"pair":"different","liquidity":99000,"volume":900,"buys":9,"sells":1},
        }
        m=metrics(rows,found)
        self.assertEqual(m["known_liquidity"],1)
        self.assertEqual(m["volume_ge_250"],2)
        self.assertEqual(m["base_pass_without_age_or_momentum"],1)
        self.assertEqual(m["missing_exact_pairs"],1)

    def test_comparison_isolated_and_error_counted(self):
        now=datetime(2026,11,13,17,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            pilot=root/"pilot.sqlite"
            out=root/"shadow.sqlite"
            with sqlite3.connect(pilot) as db:
                initialize(db)
            with sqlite3.connect(":memory:") as source:
                old=[("a","pa","t"),("b","pb","t")]
                proposed=old[:1]+[("c","pc","t")]
                found={"a":{"pair":"pa","liquidity":30000,"volume":500,"buys":5,"sells":1},
                       "b":{"pair":"pb","liquidity":None,"volume":0,"buys":0,"sells":0}}
                payload=[{"chainId":"solana","baseToken":{"address":"c"},
                          "pairAddress":"pc","priceUsd":"1","liquidity":{"usd":24000},
                          "volume":{"m5":350},"txns":{"m5":{"buys":3,"sells":1}}}]
                with patch("research_selection_shadow.adaptive_candidate_preview",
                           return_value={"proposed":proposed}):
                    with patch("research_selection_shadow.fetch",return_value=payload) as fetched:
                        x=compare_cycle(source,pilot,object(),now,old,found,out)
                fetched.assert_called_once()
                self.assertEqual(x["legacy"]["base_pass_without_age_or_momentum"],1)
                self.assertEqual(x["proposed"]["base_pass_without_age_or_momentum"],2)
                self.assertEqual(x["extra_requested"],1)
                r=report(out,24,now)
                self.assertEqual(r["cycles_successful"],1)
                self.assertEqual(r["base_filter_without_age_or_momentum"]["diff"],1)
                with sqlite3.connect(out) as comparison:
                    counts=dict(comparison.execute("SELECT cohort,COUNT(*) FROM selection_cohort_samples GROUP BY cohort").fetchall())
                self.assertEqual(counts,{"legacy":2,"proposed":2})
                with patch("research_selection_shadow.fetch",side_effect=ValueError("API unavailable")):
                    later=datetime(2026,11,13,17,1,tzinfo=timezone.utc)
                    with patch("research_selection_shadow.adaptive_candidate_preview",
                               return_value={"proposed":proposed}):
                        y=compare_cycle(source,pilot,object(),later,old,found,out)
                self.assertEqual(y["status"],"ERROR")
                r=report(out,24,later)
                self.assertEqual(r["cycles_failed"],1)
                with sqlite3.connect(pilot) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM minute_observations").fetchone()[0],0)

    def test_v2_epoch_excludes_old_comparison_but_preserves_rows(self):
        from research_selection_shadow import write_cycle, effective_cutoff
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"shadow.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE comparison_cycles(
                    observed_at TEXT PRIMARY KEY,legacy_selected INTEGER,
                    proposed_selected INTEGER,legacy_known INTEGER,
                    proposed_known INTEGER,legacy_volume INTEGER,
                    proposed_volume INTEGER,legacy_base INTEGER,
                    proposed_base INTEGER,legacy_missing INTEGER,
                    proposed_missing INTEGER,changed_pairs INTEGER,
                    extra_requested INTEGER,error TEXT)""")
                db.execute("""INSERT INTO comparison_cycles VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                    "2026-11-13T18:00:00+00:00",30,30,4,5,4,5,1,2,0,1,1,1,None))
            a={"selected":30,"known_liquidity":5,"volume_ge_250":4,
               "base_pass_without_age_or_momentum":1,"missing_exact_pairs":0}
            write_cycle(path,"2026-11-14T18:00:00+00:00",
                        a,a,0,0,samples=[])
            now=datetime(2026,11,14,18,1,tzinfo=timezone.utc)
            result=report(path,24,now)
            self.assertEqual(result["cycles_recorded"],1)
            self.assertEqual(result["selection_version"],"v2")
            self.assertEqual(result["first_cycle_utc"],"2026-11-14T18:00:00+00:00")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM comparison_cycles").fetchone()[0],2)
                self.assertEqual(effective_cutoff(db,"2026-11-13T18:01:00+00:00"),
                                 "2026-11-14T18:00:00+00:00")

if __name__=="__main__":
    unittest.main()
