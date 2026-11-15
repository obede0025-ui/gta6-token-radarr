import sqlite3
import unittest
from research_jupiter_minute_quotes import dt
from research_pre_signal_age_sensitivity import strictly_prior,summarize

class StrictAgeSensitivityTests(unittest.TestCase):
    def test_exact_pair_strict_before_and_no_future(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("""CREATE TABLE minute_observations (
                 observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
                 liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
                 pair_age_hours REAL,price_usd REAL)""")
            db.executemany("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?)",[
                ("2026-11-15T10:00:00+00:00","a","pa","QUOTE_MARKET_OBSERVED",22000,300,5,2,.25,.01),
                ("2026-11-15T10:01:00+00:00","a","pa","QUOTE_MARKET_OBSERVED",50000,800,9,1,.3,.02),
                ("2026-11-15T09:59:00+00:00","a","pb","QUOTE_MARKET_OBSERVED",90000,500,9,1,.4,.03),
            ])
            signal=dt("2026-11-15T10:01:00+00:00")
            before=strictly_prior(db,"minute_observations","a","pa",signal)
            self.assertEqual(before["liquidity_usd"],22000)
            self.assertEqual(before["lag_seconds"],60.0)
            self.assertIsNone(strictly_prior(db,"minute_observations","a","pc",signal))

    def test_thresholds_keep_censored_and_unknown_separate(self):
        common={"gate":"PASS_ROUNDTRIP_AT_MOST_3_PERCENT"}
        items=[dict(common,age_minutes=9,outcome="SL_QUOTE_OBSERVED",stress_quote_pct=-25),
               dict(common,age_minutes=20,outcome="TP_QUOTE_OBSERVED",stress_quote_pct=12),
               dict(common,age_minutes=50,outcome="TP_QUOTE_OBSERVED",stress_quote_pct=15),
               dict(common,age_minutes=None,outcome="CENSORED_BEFORE_HORIZON",stress_quote_pct=None),
               {"gate":"BLOCKED_ROUNDTRIP_OVER_3_PERCENT", "age_minutes":60,
                "outcome":"SL_QUOTE_OBSERVED","stress_quote_pct":-30}]
        result=summarize(items,(15,30))
        self.assertEqual(result["baseline_gate_passing"]["n"],4)
        self.assertEqual(result["missing_pre_signal_age"],1)
        self.assertEqual(result["age_threshold_sensitivity"][0]["kept"]["n"],2)
        self.assertEqual(result["age_threshold_sensitivity"][0]["excluded_by_age"]["SL_QUOTE_OBSERVED"],1)
        self.assertEqual(result["age_threshold_sensitivity"][1]["kept"]["n"],1)

if __name__=="__main__":unittest.main()
