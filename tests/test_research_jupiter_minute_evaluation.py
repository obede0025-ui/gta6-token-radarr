import unittest
from datetime import datetime,timedelta,timezone
from research_jupiter_minute_evaluation import evaluate,report

BASE=datetime(2026,11,12,16,0,tzinfo=timezone.utc)
def at(s):return (BASE+timedelta(seconds=s)).isoformat()
def quote(s,pct):return {"status":"QUOTE_AVAILABLE","quoted_utc":at(s),"estimated_roundtrip_pct":pct}
def pos(quotes):
    return {"mint":"mint","signal_utc":at(0),"entry_quote_utc":at(0),
            "entry_token_raw":123456,"exit_quotes":quotes}

class TestEvaluation(unittest.TestCase):
    def test_first_tp_not_best_quote(self):
        r=evaluate(pos([quote(1,-2.8652),quote(321,11.04),quote(600,26.2762)]))
        self.assertEqual(r["gate"],"PASS_ROUNDTRIP_AT_MOST_3_PERCENT")
        self.assertEqual(r["outcome"],"TP_QUOTE_OBSERVED")
        self.assertEqual(r["gross_quote_pct"],11.04)
        self.assertEqual(r["stress_quote_pct"],9.04)

    def test_weak_roundtrip_and_censored(self):
        r=evaluate(pos([quote(2,-6.9424),quote(850,-4.8642)]))
        self.assertEqual(r["gate"],"BLOCKED_ROUNDTRIP_OVER_3_PERCENT")
        self.assertEqual(r["outcome"],"CENSORED_BEFORE_HORIZON")
        self.assertIsNone(r["stress_quote_pct"])

    def test_missing_immediate_quote_unknown(self):
        r=evaluate(pos([quote(120,-1),quote(500,20)]))
        self.assertEqual(r["gate"],"UNKNOWN_NO_IMMEDIATE_SELL_QUOTE")
        self.assertEqual(r["outcome"],"TP_QUOTE_OBSERVED")

    def test_time_exit_requires_actual_quote_after_horizon(self):
        r=evaluate(pos([quote(1,-2),quote(899,3),quote(950,4)]))
        self.assertEqual(r["outcome"],"TIME_DELAYED_QUOTE")
        self.assertEqual(r["gross_quote_pct"],4)
        self.assertEqual(r["stress_quote_pct"],2)

    def test_no_quote_not_a_loss(self):
        r=evaluate(pos([]))
        self.assertEqual(r["outcome"],"NO_SELL_QUOTE")
        self.assertIsNone(r["gross_quote_pct"])

    def test_report_rejects_wrong_state(self):
        with self.assertRaises(ValueError):report({"mode":"original","positions":{}})

if __name__=="__main__":unittest.main()
