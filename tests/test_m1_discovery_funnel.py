import unittest
import sqlite3
from m1_discovery_funnel import funnel

class DiscoveryFunnelTests(unittest.TestCase):
 def setUp(self):
  self.b3={"events":[
    {"mint":"A","decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","scanned_at":"2026-11-09T18:00:00+00:00","recorded_utc":"2026-11-09T18:01:00+00:00"},
    {"mint":"A","decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","scanned_at":"2026-11-09T18:05:00+00:00","recorded_utc":"2026-11-09T18:06:00+00:00"},
    {"mint":"B","decision":"BLOCKED","reasons":["LOW_LIQ","LOW_LIQ"],"scanned_at":"2026-11-09T18:01:00+00:00"},
    {"mint":"C","decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","scanned_at":"2026-11-09T15:00:00+00:00"}]}
  self.m1={"created_utc":"2026-11-09T17:00:00+00:00","tracked":{"A":{"status":"WATCHING"}}}
  self.v32={"positions":{"A":{"status":"ACTIVE","entry_token_raw":"100","exit_quotes":[{"status":"QUOTE_AVAILABLE"}]}}}
 def test_unique_not_repeated(self):
  x=funnel(self.b3,self.m1,self.v32)
  self.assertEqual(x["summary"]["b3_approved_unique"],2)
  self.assertEqual(x["summary"]["b3_new_approved_since_m1_with_precise_timestamp"],1)
  self.assertEqual(x["summary"]["m1_new_cohort_approved_untracked"],0)
  self.assertEqual(x["summary"]["v32_with_exit_quote"],1)
 def test_reasons_deduplicated_per_event(self):
  x=funnel(self.b3,self.m1,self.v32)
  self.assertEqual(x["b3_rejection_reasons_event_counts"]["LOW_LIQ"],1)
 def test_missing_m1_is_detected(self):
  self.m1["tracked"]={}
  x=funnel(self.b3,self.m1,self.v32)
  self.assertEqual(x["summary"]["m1_new_cohort_approved_untracked"],1)
 def test_db_unique(self):
  with sqlite3.connect(":memory:") as c:
   c.execute("CREATE TABLE snapshots(token_address TEXT)")
   c.executemany("INSERT INTO snapshots VALUES(?)",[("A",),("A",),("B",)])
   self.assertEqual(funnel(self.b3,self.m1,self.v32,c)["summary"]["db_unique_tokens_all_time"],2)
 def test_invalid_state_refused(self):
  with self.assertRaises(ValueError):funnel({},self.m1,self.v32)
if __name__=="__main__":unittest.main()
