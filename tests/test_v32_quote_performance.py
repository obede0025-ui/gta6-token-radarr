import unittest
from v32_quote_performance import evaluate, report

def position(quotes, entry=True):
    return {"entry_token_raw": "123" if entry else None,
            "entry_quote_utc": "2026-11-09T17:00:00+00:00" if entry else None,
            "exit_quotes": [{"status": "QUOTE_AVAILABLE", "age_seconds": a,
                             "estimated_return_pct": r} for a, r in quotes]}

class QuotePerformanceTests(unittest.TestCase):
    def test_first_tp_not_maximum(self):
        e = evaluate(position([(145.3, -2.9145), (522.1, 11.1523), (899.2, 20.1971)]), cost_pp=2)
        self.assertEqual(e["outcome"], "TP_OBSERVED")
        self.assertAlmostEqual(e["net_quote_return_pct"], 9.1523)
    def test_sl_before_tp(self):
        self.assertEqual(evaluate(position([(200, -11), (300, 30)]))["outcome"], "SL_OBSERVED")
    def test_missing_entry_is_not_a_trade(self):
        self.assertEqual(evaluate(position([(200, 40)], entry=False))["outcome"], "NO_ENTRY_QUOTE")
    def test_horizon_quote(self):
        self.assertEqual(evaluate(position([(200, 1), (899.2, 5)]))["outcome"], "TIME_QUOTE")
    def test_censored_is_not_zero(self):
        self.assertIsNone(evaluate(position([(100, 2)]))["net_quote_return_pct"])
    def test_count_tokens_not_quotes(self):
        s = report({"positions": {"A": position([(100, 1),(300, 12)])}})
        self.assertEqual(s["summary"]["observable_scenario_exits"], 1)

if __name__ == "__main__":
    unittest.main()
