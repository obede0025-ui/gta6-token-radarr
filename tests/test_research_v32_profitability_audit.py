import unittest
from research_v32_profitability_audit import metrics, passes, evaluate_rules


class ResearchTests(unittest.TestCase):
    def test_negative_return_bounded_at_minus_100(self):
        a = [{"bounded_quote_return_pct": -100.0}, {"bounded_quote_return_pct": 10.0}]
        self.assertEqual(metrics(a)["mean_pct"], -45)

    def test_threshold_directions(self):
        row={"liquidity_usd": 30000, "entry_lag_seconds": 120}
        self.assertTrue(passes(row, ("liquidity_usd", ">=", 30000)))
        self.assertTrue(passes(row, ("entry_lag_seconds", "<=", 120)))
        self.assertFalse(passes(row, ("entry_lag_seconds", "<=", 60)))

    def test_rule_score_uses_only_train(self):
        row=lambda score: dict(liquidity_usd=35000, volume_m5=600,
                               buy_share=0.6, entry_lag_seconds=100,
                               pair_age_hours=0.2, bounded_quote_return_pct=score)
        train=[row(10)]*8
        test=[row(-99)]*4
        results=evaluate_rules(train,test,8,4)
        self.assertTrue(results)
        self.assertTrue(any(x["test"]["mean_pct"] == -99 for x in results))


if __name__ == "__main__":
    unittest.main()
