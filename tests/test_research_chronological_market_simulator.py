import unittest
from datetime import datetime,timedelta,timezone
from research_chronological_market_simulator import (
    chronosplit, qualify, scenario_return, summarize, simulate_records
)

T0=datetime(2026,11,10,tzinfo=timezone.utc)
def record(i,gain=12,collapse=False):
    return {
       "mint":f"mint{i}","pair":f"pair{i}",
       "baseline_utc":(T0+timedelta(hours=i)).isoformat(),
       "decision_to_5m_pct":gain,
       "horizon_5m_liquidity_usd":0 if collapse else 32000,
       "outcome_label":"LIQUIDITY_COLLAPSE_WITH_PRICE_LOSS" if collapse else "MOMENTUM_WITH_LIQUIDITY",
       "features":{"price_accel_pp":10,"liquidity_accel_pp":9,
                  "buy_share_accel_pp":1.2},
       "decision":{"liquidity_risk_at_3m":False},
    }

class ChronoSimulationTest(unittest.TestCase):
    def test_risk_never_overridden(self):
        r=record(0)
        r["decision"]["liquidity_risk_at_3m"]=True
        for method in ("strict","balanced_0.35","price_0.35"):
            self.assertFalse(qualify(r,method))

    def test_collapse_is_stressed_not_paper_winner(self):
        r=record(0,gain=999,collapse=True)
        self.assertEqual(scenario_return(r,4),-100)
        self.assertEqual(scenario_return(record(0,gain=12),4),8)

    def test_time_split_and_train_only_choice(self):
        data=[record(i) for i in range(100)]
        a,b=chronosplit(data)
        self.assertEqual(len(a["train"]),60)
        self.assertEqual(len(a["validation"]),20)
        self.assertEqual(len(a["pseudo_test"]),20)
        self.assertEqual(b["discarded_boundary_tokens"],0)
        original=simulate_records(data)
        modified=[record(i, gain=-99 if i>=80 else 12) for i in range(100)]
        new=simulate_records(modified)
        self.assertEqual(original["training_chosen"],new["training_chosen"])
        self.assertEqual(original["all_methods_price_marks_4pct_cost"]["strict"]["train"],
                         new["all_methods_price_marks_4pct_cost"]["strict"]["train"])
        self.assertNotEqual(original["all_methods_price_marks_4pct_cost"]["strict"]["pseudo_test"],
                            new["all_methods_price_marks_4pct_cost"]["strict"]["pseudo_test"])

    def test_small_dataset_is_not_declared_working(self):
        r=simulate_records([record(i) for i in range(6)])
        self.assertIsNone(r["training_chosen"])
        self.assertFalse(r["validation_thresholds_met"])

if __name__=="__main__":unittest.main()
