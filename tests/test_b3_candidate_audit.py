import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from b3_candidate_audit import examine

class B3CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"learning.db"
        self.now=datetime(2026,11,8,20,0,tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE snapshots (
                scanned_at TEXT, token_address TEXT, pair_address TEXT,
                name TEXT, price_usd REAL, liquidity_usd REAL, volume_m5 REAL,
                buys_m5 REAL, sells_m5 REAL, pair_age_hours REAL)""")
            c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (self.now.isoformat(),"TOKEN_A","PAIR_A","test",
                       0.001,1600,1000,10,2,3))
    def tearDown(self):
        self.tmp.cleanup()
    def test_blocks_low_liquidity(self):
        result=examine(self.db,now=self.now)
        self.assertEqual(result["counts"]["recent"],1)
        self.assertEqual(result["counts"]["eligible"],0)
        self.assertIn("LIQUIDITY_BELOW_20000",result["candidates"][0]["reasons"])
    def test_ignores_old_snapshots(self):
        result=examine(self.db,now=self.now+timedelta(hours=2))
        self.assertEqual(result["counts"]["recent"],0)
    def test_does_not_mutate_db(self):
        before=self.db.read_bytes()
        examine(self.db,now=self.now)
        self.assertEqual(self.db.read_bytes(),before)

if __name__=="__main__":
    unittest.main()
