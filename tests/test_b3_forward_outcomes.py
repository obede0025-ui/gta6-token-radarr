import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from b3_forward_outcomes import audit

class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/"market.db"
        self.state=self.root/"shadow.json"
        self.t0=datetime(2026,11,8,20,0,tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE snapshots(
                scanned_at TEXT,token_address TEXT,pair_address TEXT,price_usd REAL)""")
        self.state.write_text(json.dumps({
            "version":1,"events":[{"scanned_at":self.t0.isoformat(),
              "mint":"MINT1","pair":"PAIR1","price_usd":1.0,
              "decision":"BLOCKED","reasons":["LIQUIDITY_BELOW_20000"]}]}))
    def tearDown(self):self.tmp.cleanup()
    def insert(self,minute,price,pair="PAIR1"):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                ((self.t0+timedelta(minutes=minute)).isoformat(),"MINT1",pair,price))
    def test_premature_does_not_count(self):
        self.insert(14,10)
        result=audit(self.db,self.state,now=self.t0+timedelta(minutes=15))
        self.assertEqual(result["by_decision"]["BLOCKED"]["horizons"]["15m"]["statuses"],
                         {"MISSING_MARKET_SAMPLE":1})
    def test_valid_15m_sample(self):
        self.insert(16,1.5)
        self.insert(61,0.5)
        result=audit(self.db,self.state,now=self.t0+timedelta(minutes=62))
        horizons=result["by_decision"]["BLOCKED"]["horizons"]
        self.assertEqual(horizons["15m"]["mean_price_change_pct"],50.0)
        self.assertEqual(horizons["60m"]["mean_price_change_pct"],-50.0)
    def test_other_pool_excluded(self):
        self.insert(16,100,pair="DIFFERENT_POOL")
        result=audit(self.db,self.state,now=self.t0+timedelta(minutes=30))
        self.assertEqual(result["by_decision"]["BLOCKED"]["horizons"]["15m"]["observed_count"],0)
    def test_not_yet_due(self):
        result=audit(self.db,self.state,now=self.t0+timedelta(minutes=5))
        self.assertEqual(result["by_decision"]["BLOCKED"]["horizons"]["15m"]["statuses"],
                         {"PENDING":1})
    def test_read_only(self):
        self.insert(16,1.3)
        before=self.db.read_bytes()
        original=self.state.read_bytes()
        audit(self.db,self.state,now=self.t0+timedelta(minutes=30))
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(self.state.read_bytes(),original)

if __name__=="__main__":
    unittest.main()
