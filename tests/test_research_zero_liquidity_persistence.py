"""Regression tests for zero-liquidity persistence read-only report."""
import unittest
from research_zero_liquidity_persistence import inspect


def row(minute, status, lp, volume=0, buys=0, sells=0, pair="P"):
    return ("M", pair, f"2026-11-20T10:{minute:02d}:00Z",
            status, lp, 1, volume, buys, sells)


class PersistenceTests(unittest.TestCase):
    def test_persistent_zero_with_later_m5_activity(self):
        data = [
            row(0, "QUOTE_MARKET_OBSERVED", 1000),
            row(1, "QUOTE_MARKET_OBSERVED", 0, 20, 1, 1),
            row(2, "LIQUIDITY_UNKNOWN", None, 22, 2, 2),
            row(6, "QUOTE_MARKET_OBSERVED", 0, 10, 1, 1),
        ]
        output = inspect(data)
        self.assertEqual(output["positive_to_zero_transitions"], 1)
        self.assertEqual(output["zero_at_five_min"], 1)
        self.assertEqual(output["later_m5_volume_positive"], 1)
        self.assertEqual(output["five_min_unobserved"], 0)
        self.assertEqual(output["database_writes"], 0)

    def test_unknown_not_zero_and_recovery(self):
        data = [
            row(0, "QUOTE_MARKET_OBSERVED", 1000),
            row(1, "LIQUIDITY_UNKNOWN", None),
            row(2, "QUOTE_MARKET_OBSERVED", 0),
            row(7, "QUOTE_MARKET_OBSERVED", 500),
        ]
        output = inspect(data)
        self.assertEqual(output["positive_to_zero_transitions"], 1)
        self.assertEqual(output["positive_at_five_min"], 1)
        self.assertEqual(output["zero_at_five_min"], 0)

    def test_missing_five_min_data_is_not_persistence(self):
        data = [
            row(0, "QUOTE_MARKET_OBSERVED", 1000),
            row(1, "QUOTE_MARKET_OBSERVED", 0),
            row(3, "QUOTE_MARKET_OBSERVED", 0),
            row(7, "LIQUIDITY_UNKNOWN", None),
        ]
        output = inspect(data)
        self.assertEqual(output["five_min_unobserved"], 1)
        self.assertEqual(output["zero_at_five_min"], 0)


if __name__ == "__main__":
    unittest.main()
