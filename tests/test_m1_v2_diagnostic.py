import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from m1_v2_diagnostic import feature_history, summarize

class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"db.sqlite"
        self.now=datetime(2026,11,9,12,tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE snapshots(
                scanned_at TEXT,token_address TEXT,pair_address TEXT,
                buys_m5 REAL,sells_m5 REAL,volume_m5 REAL,liquidity_usd REAL)""")
    def tearDown(self):self.tmp.cleanup()
    def insert(self,minute,b,s,v):
        stamp=(self.now+timedelta(minutes=minute)).isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",(stamp,"mint","pair",b,s,v,30000))
    def test_exhaustion_is_research_flag(self):
        self.insert(-10,1600,1300,9000)
        self.insert(0,1500,1450,2500)
        with sqlite3.connect(self.db) as c:
            r=feature_history(c,{"mint":"mint","pair":"pair","scanned_at":self.now.isoformat()})
        self.assertTrue(r["exhaustion_research_flag"])
        self.assertLess(r["volume_change_pct"],-60)
    def test_missing_data_does_not_invent_signal(self):
        self.insert(0,1500,1450,2500)
        with sqlite3.connect(self.db) as c:
            r=feature_history(c,{"mint":"mint","pair":"pair","scanned_at":self.now.isoformat()})
        self.assertEqual(r["status"],"INSUFFICIENT_HISTORY")
    def test_group_summary_missing_not_counted_as_loss(self):
        rows=[{"group":"MICRO","observed_change_pct":None,"exit_reason":None,
               "outcome_status":"PENDING","exhaustion_research_flag":None,"history_status":"INSUFFICIENT_HISTORY"},
              {"group":"MICRO","observed_change_pct":17,"exit_reason":"TP",
               "outcome_status":"OBSERVED_PRICE_TRIGGER","exhaustion_research_flag":False,"history_status":"MEASURED"}]
        s=summarize(rows)
        self.assertEqual(s["MICRO"]["outcomes_observed"],1)
        self.assertEqual(s["MICRO"]["outcomes_missing"],1)
        self.assertEqual(s["MICRO"]["median_price_change_pct"],17)
if __name__=="__main__":
    unittest.main()
