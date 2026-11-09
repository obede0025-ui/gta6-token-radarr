import unittest
from datetime import datetime,timedelta,timezone
from m1_v3_2_forward_quotes import bootstrap,evaluate,USDC
MINT="So11111111111111111111111111111111111111112"
class ForwardQuoteTests(unittest.TestCase):
 def setUp(self):
  self.t=datetime(2026,11,9,16,tzinfo=timezone.utc)
  self.m1={"version":1,"tracked":{}}
  self.state=bootstrap(self.m1,self.t)
  self.calls=[]
 def add(self,mint=MINT,seconds=5):
  item={"mint":mint,"signal":(self.t+timedelta(seconds=seconds)).isoformat(),"status":"WATCHING"}
  self.m1["tracked"][mint]=item
 def quote(self,a,b,n):
  self.calls.append((a,b,n))
  if a==USDC:return {"output_raw":1000000}
  return {"output_raw":24000000}
 def test_bootstrap_ignores_old_tokens(self):
  self.add()
  st=bootstrap(self.m1,self.t+timedelta(seconds=10))
  r=evaluate(st,self.m1,self.t+timedelta(seconds=12),self.quote)
  self.assertEqual(r["jupiter_requests"],0)
 def test_entry_and_sell_preserve_quantity(self):
  self.add()
  r=evaluate(self.state,self.m1,self.t+timedelta(seconds=10),self.quote)
  self.assertEqual(r["jupiter_requests"],2)
  self.assertEqual(self.calls[1],(MINT,USDC,1000000))
  p=self.state["positions"][MINT]
  self.assertEqual(p["exit_quotes"][0]["estimated_return_pct"],"-4.0000")
  self.assertEqual(p["entry_quote_utc"],(self.t+timedelta(seconds=10)).isoformat())
 def test_no_repeat_under_sixty_seconds(self):
  self.add()
  evaluate(self.state,self.m1,self.t+timedelta(seconds=10),self.quote)
  r=evaluate(self.state,self.m1,self.t+timedelta(seconds=30),self.quote)
  self.assertEqual(r["jupiter_requests"],0)
 def test_exit_refresh_same_entry_quantity(self):
  self.add()
  evaluate(self.state,self.m1,self.t+timedelta(seconds=10),self.quote)
  r=evaluate(self.state,self.m1,self.t+timedelta(seconds=75),self.quote)
  self.assertEqual(r["jupiter_requests"],1)
  self.assertEqual(self.calls[-1],(MINT,USDC,1000000))
 def test_no_old_entries_or_exits(self):
  self.add(seconds=-500)
  r=evaluate(self.state,self.m1,self.t,self.quote)
  self.assertEqual(r["jupiter_requests"],0)
  self.assertEqual(self.state["positions"][MINT]["status"],"SKIPPED_STALE")
 def test_expiry_without_fake_fill(self):
  self.add()
  evaluate(self.state,self.m1,self.t+timedelta(seconds=10),self.quote)
  r=evaluate(self.state,self.m1,self.t+timedelta(minutes=16),self.quote)
  self.assertEqual(r["jupiter_requests"],0)
  self.assertEqual(self.state["positions"][MINT]["status"],"WINDOW_EXPIRED")
 def test_invalid_quote_does_not_make_position(self):
  self.add()
  def fail(a,b,n):raise ValueError("Failed route")
  r=evaluate(self.state,self.m1,self.t+timedelta(seconds=10),fail)
  self.assertEqual(r["jupiter_requests"],1)
  self.assertEqual(self.state["positions"][MINT]["status"],"ENTRY_UNAVAILABLE")
 def test_only_two_active_positions(self):
  for i in range(4):self.add(f"mint{i}")
  r=evaluate(self.state,self.m1,self.t+timedelta(seconds=10),self.quote)
  self.assertEqual(r["jupiter_requests"],4)
  self.assertEqual(r["active"],2)
if __name__=="__main__":unittest.main()
