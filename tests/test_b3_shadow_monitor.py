import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from b3_shadow_monitor import run

class B3ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"db.sqlite"
        self.state=Path(self.tmp.name)/"b3_state.json"
        self.now=datetime(2026,11,8,20,0,tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE snapshots (scanned_at TEXT, token_address TEXT,
                pair_address TEXT,name TEXT,price_usd REAL,liquidity_usd REAL,
                volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            self.add(self.now-timedelta(minutes=5),"initial")
    def add(self,dt,name):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (dt.isoformat(),name,"PAIR","token",0.001,1200,300,5,2,2))
    def tearDown(self):self.tmp.cleanup()
    def test_bootstrap_skips_history(self):
        result=run(self.db,self.state,now=self.now)
        self.assertTrue(result["bootstrapped"])
        self.assertEqual(result["new"],0)
    def test_future_only_and_idempotent(self):
        run(self.db,self.state,now=self.now)
        self.add(self.now+timedelta(minutes=1),"next")
        result=run(self.db,self.state,now=self.now+timedelta(minutes=1))
        self.assertEqual(result["new"],1)
        self.assertEqual(result["decisions"]["BLOCKED"],1)
        self.assertEqual(run(self.db,self.state,now=self.now+timedelta(minutes=1))["new"],0)
    def test_db_is_untouched(self):
        run(self.db,self.state,now=self.now)
        before=self.db.read_bytes()
        self.add(self.now+timedelta(minutes=1),"next")
        before=self.db.read_bytes()
        run(self.db,self.state,now=self.now+timedelta(minutes=1))
        self.assertEqual(self.db.read_bytes(),before)
    def test_old_data_not_replayed(self):
        run(self.db,self.state,now=self.now)
        self.add(self.now+timedelta(minutes=1),"next")
        result=run(self.db,self.state,now=self.now+timedelta(minutes=40))
        self.assertEqual(result["new"],0)

if __name__=="__main__":unittest.main()
