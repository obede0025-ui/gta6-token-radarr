import json,sqlite3,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from m1_micro_15m_research import evaluate,micro_candidate
class M1Tests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
  self.db=self.root/"db.sqlite";self.shadow=self.root/"state.json"
  self.t0=datetime(2026,11,9,12,tzinfo=timezone.utc)
  with sqlite3.connect(self.db) as c:
   c.execute("CREATE TABLE snapshots(scanned_at TEXT,token_address TEXT,pair_address TEXT,price_usd REAL,liquidity_usd REAL,buys_m5 REAL,sells_m5 REAL,average_trade_usd REAL)")
  self.shadow.write_text(json.dumps({"version":1,"events":[{"mint":"A","pair":"P","scanned_at":self.t0.isoformat(),"price_usd":1,"decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW"}]}))
  self.add(0,1)
 def tearDown(self):self.t.cleanup()
 def add(self,min_,price,pair="P"):
  with sqlite3.connect(self.db) as c:c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)",((self.t0+timedelta(minutes=min_)).isoformat(),"A",pair,price,23000,1200,1150,.5))
 def test_micro_classification(self):
  self.assertTrue(micro_candidate(1200,1150,.5))
  self.assertFalse(micro_candidate(1200,100,20))
 def test_trigger_at_5m(self):
  self.add(5,1.2)
  r=evaluate(self.db,self.shadow,now=self.t0+timedelta(minutes=20))
  self.assertEqual(r["outcomes"][0]["exit_reason"],"TP")
 def test_no_future(self):
  self.add(16,.5)
  r=evaluate(self.db,self.shadow,now=self.t0+timedelta(minutes=10))
  self.assertEqual(r["outcomes"][0]["status"],"PENDING")
 def test_missing_exit_not_invented(self):
  r=evaluate(self.db,self.shadow,now=self.t0+timedelta(minutes=20))
  self.assertEqual(r["outcomes"][0]["status"],"MISSING_EXIT_SAMPLE")
 def test_same_pair_only(self):
  self.add(15,1.9,"OTHER")
  r=evaluate(self.db,self.shadow,now=self.t0+timedelta(minutes=20))
  self.assertEqual(r["outcomes"][0]["status"],"MISSING_EXIT_SAMPLE")
if __name__=="__main__":unittest.main()
