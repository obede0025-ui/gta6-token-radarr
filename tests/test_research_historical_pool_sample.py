"""Tests for historical verification sampling (not a trading signal)."""
import unittest
from research_historical_pool_sample import select_cases


def r(mint, m, status, lp):
    return (mint, "P", f"2026-11-20T10:{m:02d}:00Z", status, lp, 1)


class PoolSampleTests(unittest.TestCase):
    def test_strata_and_repeated_transition_counts(self):
        rows = [
            r("A", 0, "QUOTE_MARKET_OBSERVED", 30000),
            r("A", 1, "QUOTE_MARKET_OBSERVED", 0),
            r("A", 2, "LIQUIDITY_UNKNOWN", None),
            r("A", 6, "QUOTE_MARKET_OBSERVED", 0),
            r("B", 0, "QUOTE_MARKET_OBSERVED", 30000),
            r("B", 1, "QUOTE_MARKET_OBSERVED", 0),
            r("B", 6, "QUOTE_MARKET_OBSERVED", 40000),
            r("C", 0, "QUOTE_MARKET_OBSERVED", 30000),
            r("C", 1, "QUOTE_MARKET_OBSERVED", 0),
            r("C", 3, "QUOTE_MARKET_OBSERVED", 0),
            r("D", 0, "QUOTE_MARKET_OBSERVED", 0),
        ]
        output = select_cases(rows)
        counts = output["population_by_outcome"]
        self.assertEqual(counts["STILL_ZERO_AT_5M"], 1)
        self.assertEqual(counts["RECOVERED_AT_5M"], 1)
        self.assertEqual(counts["UNOBSERVED_AT_5M"], 1)
        self.assertEqual(len(output["sample"]), 3)
        self.assertEqual(output["database_writes"], 0)
        self.assertEqual(output["trades_executed"], 0)

    def test_order_independent_sample(self):
        rows = [r("A", 0, "QUOTE_MARKET_OBSERVED", 50000),
                r("A", 1, "QUOTE_MARKET_OBSERVED", 0),
                r("A", 6, "QUOTE_MARKET_OBSERVED", 0)]
        self.assertEqual(select_cases(rows)["sample"],
                         select_cases(list(reversed(rows)))["sample"])


if __name__ == "__main__":
    unittest.main()
