import sqlite3
import tempfile
import unittest
from pathlib import Path
from research_discovery_exit_risk_v28 import audit,summary

class RiskTests(unittest.TestCase):
    def fixture(self,root):
        out=root/"quotes.sqlite"
        source=root/"source.sqlite"
        with sqlite3.connect(out) as db:
            db.execute("""CREATE TABLE first_signals(
                 mint TEXT,pair TEXT,signal_utc TEXT,cohorts TEXT,
                 entry_liquidity_usd REAL,entry_sell_usdc_raw TEXT,
                 buy_status TEXT)""")
            db.execute("""CREATE TABLE quote_followups(
                 mint TEXT,pair TEXT,horizon_sec INTEGER,
                 quote_usdc_raw TEXT,status TEXT)""")
            for mint,liq in (("aaa",25000),("bbb",35000)):
                db.execute("INSERT INTO first_signals VALUES(?,?,?,?,?,?,?)",
                   (mint,"pool_"+mint,"2026-11-16T19:00:00+00:00",
                    "ACTIVITY_FIRST+FEED_ORDER",liq,"9700000","BUY_AND_INITIAL_SELL_QUOTED"))
            for h in (60,300,900):
                db.execute("INSERT INTO quote_followups VALUES(?,?,?,?,?)",
                    ("aaa","pool_aaa",h,
                     "100000" if h==900 else "10000000","JUPITER_SELL_ROUTE_QUOTED"))
                if h!=300:
                    db.execute("INSERT INTO quote_followups VALUES(?,?,?,?,?)",
                        ("bbb","pool_bbb",h,"12000000","JUPITER_SELL_ROUTE_QUOTED"))
                else:
                    db.execute("INSERT INTO quote_followups VALUES(?,?,?,?,?)",
                        ("bbb","pool_bbb",h,None,"SELL_ROUTE_UNAVAILABLE"))
        with sqlite3.connect(source) as db:
            db.execute("""CREATE TABLE quality_cohort_samples(
                 mint TEXT,pair TEXT,observed_at TEXT,cohort TEXT,
                 volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
                 liquidity_usd REAL,pair_age_hours REAL,price_usd REAL,
                 momentum_pass INTEGER)""")
            for mint,liq,buy,sell in (("aaa",25000,9,1),("bbb",35000,3,7)):
                db.execute("INSERT INTO quality_cohort_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (mint,"pool_"+mint,"2026-11-16T19:00:00+00:00",
                      "FEED_ORDER",1000,buy,sell,liq,1,0.01,1))
        return source,out

    def test_policy_no_per_token_hindsight_and_missing_quote(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source,out=self.fixture(root)
            before=(source.read_bytes(),out.read_bytes())
            result=audit(source,out,examples=2)
            self.assertEqual(result["unique_first_signal_pairs"],2)
            self.assertEqual(result["fixed_horizon_comparison_before_network_fees"]["300"]["valid_quotes"],1)
            self.assertEqual(result["fixed_horizon_comparison_before_network_fees"]["900"]["worst_pct"],-99.)
            self.assertEqual(result["quote_missing_reasons"]["300_SELL_ROUTE_UNAVAILABLE"],1)
            self.assertEqual(result["predeclared_signal_time_groups"]["buy_share_at_least_65"]["count"],1)
            self.assertEqual((source.read_bytes(),out.read_bytes()),before)

    def test_missing_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(audit(Path(td)/"missing",Path(td)/"none")["status"],"NO_QUOTE_DATABASE")
        r=summary([-5,None,10],3)
        self.assertEqual((r["valid_quotes"],r["unverified"],r["mean_pct"]),(2,1,2.5))

    def test_invalid_examples(self):
        with self.assertRaises(ValueError):audit(examples=-1)

if __name__=="__main__":unittest.main()
