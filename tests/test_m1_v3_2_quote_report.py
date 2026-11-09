import unittest
from m1_v3_2_quote_report import build_report

MINT="3AgytyBs815x"
class QuoteReportTests(unittest.TestCase):
 def row(self):
  return {"mint":MINT,"status":"ACTIVE","signal_utc":"2026-11-09T17:46:27+00:00",
          "entry_quote_utc":"2026-11-09T17:48:52+00:00",
          "entry_lag_seconds":145.3,"entry_token_raw":"10000",
          "exit_quotes":[
             {"checked_utc":"2026-11-09T17:48:52+00:00","age_seconds":145.3,"status":"QUOTE_AVAILABLE","estimated_sell_usdc":"24.271376"},
             {"checked_utc":"2026-11-09T17:49:55+00:00","age_seconds":209,"status":"QUOTE_AVAILABLE","estimated_sell_usdc":"24.649871"},
             {"checked_utc":"2026-11-09T17:50:57+00:00","age_seconds":270.3,"status":"QUOTE_AVAILABLE","estimated_sell_usdc":"25.102389"}]}
 def wrap(self,rows):
  return {"version":1,"mode":"M1_V3_2_FORWARD_QUOTES","positions":rows}
 def test_known_token_quotes(self):
  report=build_report(self.wrap({MINT:self.row()}))
  p=report["positions"][0]
  self.assertEqual(p["best_quote"]["change_pct"],"0.4096")
  self.assertEqual(p["worst_quote"]["change_pct"],"-2.9145")
  self.assertEqual(p["first_quoted_breakeven"]["seconds_after_signal"],270.3)
  self.assertEqual(p["last_valid_quote_within_window"]["usdc"],"25.102389")
 def test_stale_token_not_fake_filled(self):
  stale={"mint":"STALE","status":"SKIPPED_STALE","entry_token_raw":None,"exit_quotes":[]}
  report=build_report(self.wrap({"STALE":stale}))
  self.assertEqual(report["summary"]["hypothetical_entries"],0)
  self.assertIsNone(report["positions"][0]["best_quote"])
 def test_missing_routes_and_window(self):
  p=self.row()
  p["exit_quotes"].append({"checked_utc":"2026-11-09T17:52:00+00:00","age_seconds":333,"status":"UNAVAILABLE"})
  p["exit_quotes"].append({"checked_utc":"2026-11-09T18:10:00+00:00","age_seconds":1413,"status":"QUOTE_AVAILABLE","estimated_sell_usdc":"100"})
  x=build_report(self.wrap({MINT:p}))["positions"][0]
  self.assertEqual(x["unavailable_exit_quotes"],1)
  self.assertEqual(x["quotes_outside_15min_excluded"],1)
  self.assertEqual(x["best_quote"]["usdc"],"25.102389")
 def test_sort_observations_not_insertion(self):
  p=self.row()
  p["exit_quotes"]=list(reversed(p["exit_quotes"]))
  x=build_report(self.wrap({MINT:p}))["positions"][0]
  self.assertEqual(x["first_quoted_breakeven"]["seconds_after_signal"],270.3)
 def test_invalid_report_format(self):
  with self.assertRaises(ValueError):build_report({"version":2,"mode":"M1_V3_2_FORWARD_QUOTES","positions":{}})
 def test_empty_report(self):
  result=build_report(self.wrap({}))
  self.assertEqual(result["summary"]["candidates"],0)
if __name__=="__main__":unittest.main()
