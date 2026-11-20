"""Tests for liquidity-to-zero observation research."""
import unittest
from research_liquidity_zero_transitions import analyze_rows


class TransitionResearchTests(unittest.TestCase):
    def test_zero_recovery_and_other_pair_are_distinct(self):
        def row(pair, minute, lp, price=1, volume=100):
            return ("mintA", pair, "2026-11-20T10:%02d:00Z" % minute,
                    lp, price, volume, 10, 5)
        data = [
            row("P", 0, 30000, 1, 100),
            row("P", 1, 25000, 0.8, 120),
            row("P", 2, 0, 0.7, 100),
            row("P", 3, 0, 0.6, 100),
            row("P", 4, 10000, 0.5, 100),
            row("Q", 2, 40000, 1, 100),
            ("mintB", "R", "2026-11-20T10:01:00Z", 0, 1, 100, 1, 1),
        ]
        result = analyze_rows(data, [("mintA", "Q")])
        self.assertEqual(result["pairs_with_zero"], 2)
        self.assertEqual(result["always_zero"], 1)
        self.assertEqual(result["pairs_positive_to_zero"], 1)
        self.assertEqual(result["pairs_with_zero_to_positive"], 1)
        self.assertEqual(result["median_gap_before_first_zero_seconds"], 60)
        self.assertEqual(result["alternate_pair_with_positive_liquidity_near_transition"], 1)
        self.assertEqual(result["pre_transition_price_samples"], 1)
        self.assertAlmostEqual(result["median_price_change_before_transition_pct"], -20, places=2)
        self.assertEqual(result["database_writes"], 0)

    def test_no_false_transition_from_initial_zero(self):
        data = [
            ("M", "P", "2026-11-20T10:00:00Z", 0, 1, 20, 1, 1),
            ("M", "P", "2026-11-20T10:01:00Z", 0, 1, 20, 1, 1),
        ]
        result = analyze_rows(data)
        self.assertEqual(result["pairs_positive_to_zero"], 0)
        self.assertEqual(result["always_zero"], 1)


if __name__ == "__main__":
    unittest.main()
