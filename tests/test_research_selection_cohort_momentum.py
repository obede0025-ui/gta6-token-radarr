import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from research_selection_shadow import cohort_rows, write_cycle
from research_selection_cohort_momentum import analyze, report

class SelectionCohortTests(unittest.TestCase):
    def test_missing_liquidity_never_qualifies(self):
        stamp="2026-11-13T18:00:00+00:00"
        rows=cohort_rows(stamp,[("x","p","t")],
            {"x":{"pair":"p","price":1.0,"liquidity":None,
                  "volume":900,"buys":20,"sells":1,"created_ms":0}},"proposed")
        self.assertEqual(rows[0][4],"LIQUIDITY_UNKNOWN")
        actual=analyze(rows)
        self.assertEqual(actual["cohorts"]["proposed"]["base_pass_with_age"],0)

    def test_pair_level_causal_momentum_and_first_only(self):
        start=datetime(2026,11,13,18,0,tzinfo=timezone.utc)
        rows=[]
        for i,price in enumerate((1.0,1.02,1.06,1.09)):
            stamp=(start+timedelta(minutes=i*5)).isoformat(timespec="seconds")
            rows.append((stamp,"proposed","token","pair","QUOTE_MARKET_OBSERVED",
                        price,30000,500,20,2,1))
            rows.append((stamp,"legacy","other","pair2","LIQUIDITY_UNKNOWN",
                        price,None,500,20,2,1))
        result=analyze(rows)
        new=result["cohorts"]["proposed"]
        self.assertEqual(new["first_unique_research_signals"],1)
        self.assertEqual(new["base_plus_spaced_momentum_matches"],2)
        self.assertEqual(result["comparison"]["proposed_only_signal_pairs"],1)
        self.assertEqual(result["cohorts"]["legacy"]["first_unique_research_signals"],0)

    def test_db_isolated_and_report_uses_only_new_observations(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"shadow.sqlite"
            now=datetime(2026,11,13,18,tzinfo=timezone.utc)
            empty=report(path,24,now)
            self.assertEqual(empty["verdict"],"WAITING_FOR_FIRST_SAMPLE")
            good={"selected":1,"known_liquidity":1,"volume_ge_250":1,
                  "base_pass_without_age_or_momentum":1,"missing_exact_pairs":0}
            stamp=now.isoformat(timespec="seconds")
            samples=[(stamp,"legacy","a","p","QUOTE_MARKET_OBSERVED",
                     1,30000,500,12,1,1)]
            write_cycle(path,stamp,good,good,0,0,samples=samples)
            out=report(path,24,now)
            self.assertEqual(out["cohorts"]["legacy"]["observations"],1)
            self.assertEqual(out["cohorts"]["proposed"]["observations"],0)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM selection_cohort_samples").fetchone()[0],1)

if __name__=="__main__":
    unittest.main()
