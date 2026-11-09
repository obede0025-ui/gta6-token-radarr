import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from exit_risk_v63 import audit, classify

class ExitRiskTests(unittest.TestCase):
    def test_critical_and_normal(self):
        self.assertEqual(classify({"status": "QUOTE_AVAILABLE", "estimated_exit_usdc": "0.20"}, Decimal(25), False)[0], "CRITICAL")
        self.assertEqual(classify({"status": "QUOTE_AVAILABLE", "estimated_exit_usdc": "24.60"}, Decimal(25), False)[0], "NORMAL")
    def test_rate_limit_is_unknown_not_loss(self):
        status, loss = classify({"status": "HTTP_429", "estimated_exit_usdc": None}, Decimal(25), False)
        self.assertEqual(status, "UNAVAILABLE_HTTP_429")
        self.assertIsNone(loss)
    def test_stale_precedence(self):
        status, loss = classify({"status": "QUOTE_AVAILABLE", "estimated_exit_usdc": "0.20"}, Decimal(25), True)
        self.assertEqual((status, loss), ("STALE_QUOTE", None))
    def test_report_is_not_mutated(self):
        now = datetime.now(timezone.utc)
        report = {"checked_utc": (now - timedelta(hours=1)).isoformat(), "stake_usdc": "25",
                  "positions": [{"mint": "abc", "positions": [{"strategy": "A", "status": "QUOTE_AVAILABLE", "estimated_exit_usdc": "1"}]}]}
        result = audit(report, now=now)
        self.assertTrue(result["stale"])
        self.assertEqual(result["positions"][0]["risk"], "STALE_QUOTE")
        self.assertEqual(report["positions"][0]["positions"][0]["estimated_exit_usdc"], "1")

if __name__ == "__main__":
    unittest.main()
