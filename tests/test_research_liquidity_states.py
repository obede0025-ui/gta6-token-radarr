"""Offline checks: no missing or zero liquidity is falsely confirmed."""
import unittest
from research_liquidity_states import (
    classify_liquidity, CONFIRMED_POSITIVE, ZERO_UNVERIFIED, UNKNOWN, INVALID
)


class LiquidityStateTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(
            classify_liquidity("QUOTE_MARKET_OBSERVED", 20000, 0.03),
            CONFIRMED_POSITIVE
        )

    def test_zero_not_a_confirmed_crash(self):
        self.assertEqual(
            classify_liquidity("QUOTE_MARKET_OBSERVED", 0, 0.03),
            ZERO_UNVERIFIED
        )

    def test_unknown_status_and_missing_values(self):
        for status, lp, price in [
            ("LIQUIDITY_UNKNOWN", None, 0.02),
            ("MARKET_DATA_UNAVAILABLE", None, None),
            ("QUOTE_MARKET_OBSERVED", None, 0.02),
            ("QUOTE_MARKET_OBSERVED", 5000, None),
            ("SOMETHING_NEW", 5000, 0.02)
        ]:
            with self.subTest(status=status, lp=lp, price=price):
                self.assertEqual(classify_liquidity(status, lp, price), UNKNOWN)

    def test_invalid_numeric_values(self):
        for lp in (-1, float("inf"), float("nan")):
            with self.subTest(lp=lp):
                self.assertEqual(
                    classify_liquidity("QUOTE_MARKET_OBSERVED", lp, 1),
                    INVALID
                )


if __name__ == "__main__":
    unittest.main()
