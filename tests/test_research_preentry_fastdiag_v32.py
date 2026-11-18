import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from research_preentry_fastdiag_v32 import diagnose

NOW=datetime(2026,11,18,21,0,tzinfo=timezone.utc)
class Tests(unittest.TestCase):
    def setup_dir(self):
        td=tempfile.TemporaryDirectory(); root=Path(td.name)
        db=root/"learning.db";state=root/"portfolio.json"
        with sqlite3.connect(db) as c:
            c.execute("""CREATE TABLE snapshots(
                scanned_at TEXT,token_address TEXT,pair_address TEXT,
                liquidity_usd REAL, volume_m5 REAL, buys_m5 REAL,sells_m5 REAL)""")
        state.write_text(json.dumps({"version":1,"open":{"A":{},"B":{},"C":{}}}))
        return td,db,state

    def test_shadow_selector_ignores_capacity_without_changing_state(self):
        from research_preentry_fastdiag_v32 import shadow_without_capacity
        td,db,state=self.setup_dir()
        with td:
            original={"open":{"A":{"old":{}}, "B":{"old2":{}}, "C":{}},
                      "traded":{"A":["already"],"B":[],"C":[]}}
            def capture(conn,isolated,now,limit):
                self.assertEqual(isolated["open"],{"A":{},"B":{},"C":{}})
                self.assertEqual(isolated["traded"],original["traded"])
                return [{"strategy":"A","mint":"new"}]
            with patch("research_preentry_fastdiag_v32.select_candidates",side_effect=capture):
                self.assertEqual(len(shadow_without_capacity(db,original,NOW)),1)
            self.assertEqual(len(original["open"]["A"]),1)

    def test_diagnoses_capacity_as_distinct_from_momentum(self):
        td,db,state=self.setup_dir()
        with td:
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
                    (NOW.isoformat(timespec="seconds"),"A"*44,"B"*44,30000,1000,20,1))
            with (patch("research_preentry_fastdiag_v32.selection",return_value=[]),
                  patch("research_preentry_fastdiag_v32.shadow_without_capacity",
                        return_value=[{"strategy":"B"}])):
                r=diagnose(db,state,NOW)
            self.assertIn("PORTFOLIO_CAPACITY_BLOCKS_CANDIDATES",r["diagnosis"])
            self.assertEqual(r["shadow_strategies"]["B"],1)

    def test_missing_inputs_fail_closed(self):
        r=diagnose("/no/such/db","/no/such/state",NOW)
        self.assertEqual(r["diagnosis"],["MISSING_DATABASE_OR_STATE"])
        self.assertTrue(r["no_trades"])

    def test_recent_but_filtered(self):
        td,db,state=self.setup_dir()
        with td:
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
                    (NOW.isoformat(timespec="seconds"),"A"*44,"B"*44,1000,50,1,5))
            with patch("research_preentry_fastdiag_v32.selection",return_value=[]):
                r=diagnose(db,state,NOW)
            self.assertEqual(r["last_12m"]["snapshots"],1)
            self.assertIn("MARKET_FILTERS_REJECT_RECENT_DATA",r["diagnosis"])
            self.assertEqual(r["selected_candidates"],0)

    def test_stale_database(self):
        td,db,state=self.setup_dir()
        with td:
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
                    ((NOW-timedelta(hours=2)).isoformat(timespec="seconds"),
                    "A"*44,"B"*44,30000,1000,20,1))
            with patch("research_preentry_fastdiag_v32.selection",return_value=[]):
                r=diagnose(db,state,NOW)
            self.assertIn("SCANNER_DATA_STALE",r["diagnosis"])
            self.assertIn("NO_RECENT_SNAPSHOTS",r["diagnosis"])

    def test_market_pass_but_momentum_filters_fail(self):
        td,db,state=self.setup_dir()
        with td:
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
                    (NOW.isoformat(timespec="seconds"),"A"*44,"B"*44,30000,1000,20,1))
            with patch("research_preentry_fastdiag_v32.selection",return_value=[]):
                r=diagnose(db,state,NOW)
            self.assertIn("MOMENTUM_OR_PORTFOLIO_FILTERS_REJECT_ALL",r["diagnosis"])

if __name__=="__main__":unittest.main()
