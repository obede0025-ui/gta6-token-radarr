import unittest
from datetime import datetime,timedelta,timezone
from m1_v3_forward_monitor import bootstrap,advance
class M1V3Tests(unittest.TestCase):
 def setUp(self):
  self.t=datetime(2026,11,9,12,tzinfo=timezone.utc)
  self.events=[]
  self.shadow={"version":1,"events":self.events}
  self.state=bootstrap(self.shadow,self.t)
 def add(self,mint,offset=1):
  self.events.append({"mint":mint,"pair":"P","scanned_at":(self.t+timedelta(seconds=offset)).isoformat(),"decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","price_usd":1})
 def fetch(self,m,p):return {"price":1.2,"liquidity":25000}
 def test_bootstrap_does_not_replay(self):
  self.add("OLD")
  state=bootstrap(self.shadow,self.t+timedelta(seconds=2))
  self.assertEqual(advance(state,self.shadow,self.t+timedelta(seconds=3),self.fetch)["tracked"],0)
 def test_trigger_is_observation(self):
  self.add("NEW")
  advance(self.state,self.shadow,self.t+timedelta(seconds=3),self.fetch)
  self.assertEqual(self.state["tracked"]["NEW"]["status"],"OBSERVED_PRICE_TRIGGER")
 def test_three_request_limit(self):
  for i in range(7):self.add(str(i))
  result=advance(self.state,self.shadow,self.t+timedelta(seconds=3),self.fetch)
  self.assertEqual(result["requests"],3)
  self.assertEqual(result["tracked"],7)
 def test_expired_signal(self):
  self.add("old")
  result=advance(self.state,self.shadow,self.t+timedelta(minutes=20),self.fetch)
  self.assertEqual(result["requests"],0)
  self.assertEqual(self.state["tracked"]["old"]["status"],"MISSED_WINDOW")
 def test_cooldown(self):
  self.add("NEW")
  advance(self.state,self.shadow,self.t+timedelta(seconds=3),lambda m,p:{"price":1,"liquidity":25000})
  self.assertEqual(advance(self.state,self.shadow,self.t+timedelta(seconds=30),self.fetch)["requests"],0)
if __name__=="__main__":unittest.main()
