import unittest
from datetime import datetime,timedelta,timezone
from research_observer_health_v34 import metrics

NOW=datetime(2026,11,18,20,0,tzinfo=timezone.utc)
def base():
    return {"version":1,"started_utc":NOW.isoformat(),"signals":{},
            "paper_orders_executed":0,"live_orders_executed":0}
def item(ago=0):
    return {"first_seen_utc":(NOW-timedelta(minutes=ago)).isoformat(),
            "followups":{},"buy_raw_out":"123",
            "paper_trade_approval":"BLOCK","live_trade_approval":"BLOCK",
            "evidence_state":"BLOCKED"}
def report():
    return {"checked_utc":NOW.isoformat()}
class Tests(unittest.TestCase):
    def test_missing_ledger_critical(self):
        r=metrics(None,report(),NOW)
        self.assertEqual(r["status"],"ERROR")
        self.assertEqual(r["signal_count"],0)

    def test_clean_zero_signals_is_not_a_profit_claim(self):
        r=metrics(base(),report(),NOW)
        self.assertEqual(r["status"],"OK")
        self.assertFalse(r["sample_sufficient"])
        self.assertIsNone(r["quote_return_average_pct_observed_only"])

    def test_quote_vs_failed_routes_are_separate(self):
        state=base()
        a=item(15)
        a["followups"]={"300":{"status":"JUPITER_SELL_ROUTE_QUOTED",
                       "quote_return_pct_before_network_fees":"8.5"},
                        "900":{"status":"SELL_ROUTE_UNAVAILABLE"}}
        state["signals"]["a"]=a
        r=metrics(state,report(),NOW)
        self.assertEqual(r["quote_samples"],1)
        self.assertEqual(r["sell_unavailable"],1)
        self.assertEqual(r["quote_return_average_pct_observed_only"],"8.500")
        self.assertFalse(r["sample_sufficient"])

    def test_old_unresolved_alert_and_stale_report(self):
        state=base();state["signals"]["x"]=item(30)
        old={"checked_utc":(NOW-timedelta(minutes=30)).isoformat()}
        r=metrics(state,old,NOW)
        self.assertIn("FOLLOWUPS_OVERDUE",r["alerts"])
        self.assertIn("REPORT_STALE_OR_INVALID",r["alerts"])
        self.assertEqual(r["followups_overdue"],1)

    def test_would_block_trade_counter_and_approval(self):
        state=base();state["paper_orders_executed"]=1
        state["signals"]["x"]=item()
        state["signals"]["x"]["live_trade_approval"]="PASS"
        r=metrics(state,report(),NOW)
        self.assertIn("UNEXPECTED_TRADING_COUNTER",r["alerts"])
        self.assertIn("ENTRY_PERMISSION_NOT_BLOCKED",r["alerts"])

    def test_invalid_return_not_counted_as_profit(self):
        state=base()
        x=item()
        x["followups"]["300"]={"status":"JUPITER_SELL_ROUTE_QUOTED",
                    "quote_return_pct_before_network_fees":"NaN"}
        state["signals"]["x"]=x
        r=metrics(state,report(),NOW)
        self.assertIn("INVALID_QUOTE_RETURN",r["alerts"])
        self.assertIsNone(r["quote_return_average_pct_observed_only"])

if __name__=="__main__":unittest.main()
