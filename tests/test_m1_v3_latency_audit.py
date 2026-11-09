import unittest
from m1_v3_latency_audit import diagnose, delta_seconds, percentile

class LatencyAuditTests(unittest.TestCase):
 def setUp(self):
  self.mint="mint"
  self.b3={"events":[
   {"decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","mint":"mint","scanned_at":"2026-11-09T17:00:00Z"},
   {"decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW","mint":"mint","scanned_at":"2026-11-09T17:05:00+00:00"}
  ]}
  self.m1={"tracked":{"mint":{"signal":"2026-11-09T17:00:00+00:00","status":"WATCHING",
              "samples":[{"at":"2026-11-09T17:01:00+00:00"}]}}}
  self.v32={"positions":{"mint":{"status":"ACTIVE","entry_quote_utc":"2026-11-09T17:02:00+00:00",
   "entry_token_raw":"10000","entry_lag_seconds":120,"exit_quotes":[{"checked_utc":"2026-11-09T17:03:00+00:00"}]}}}
 def test_b3_dedupe_and_delay(self):
  r=diagnose(self.b3,self.m1,self.v32)
  self.assertEqual(r["summary"]["b3_approved_events"],2)
  self.assertEqual(r["summary"]["b3_unique_tokens"],1)
  self.assertEqual(r["observations"][0]["b3_to_first_m1_observation_seconds"],60)
 def test_entry_and_exit_lag(self):
  r=diagnose(self.b3,self.m1,self.v32)
  self.assertEqual(r["observations"][0]["v32_entry_lag_seconds"],120)
  self.assertEqual(r["observations"][0]["v32_first_exit_lag_seconds"],180)
  self.assertEqual(r["summary"]["v32_entries_quoted"],1)
 def test_no_positions_not_an_error(self):
  r=diagnose(self.b3,self.m1,{"positions":{}})
  self.assertEqual(r["observations"][0]["v32_status"],"NOT_RECORDED")
  self.assertIsNone(r["summary"]["m1_to_v32_entry_quote_median_seconds"])
 def test_mixed_iso_formats(self):
  self.assertEqual(delta_seconds("2026-11-09T17:01:00+00:00","2026-11-09T17:00:00Z"),60)
 def test_missing_and_invalid_time(self):
  self.assertIsNone(delta_seconds("bad","2026-11-09T17:00:00Z"))
  self.assertIsNone(percentile([],50))
 def test_reject_bad_data(self):
  with self.assertRaises(ValueError):
   diagnose({},self.m1,self.v32)
if __name__=="__main__":unittest.main()
