import unittest
from datetime import datetime,timedelta,timezone
from research_risk_gate_v29 import assess,old

NOW=datetime(2026,11,18,19,20,tzinfo=timezone.utc)
STAMP=NOW.isoformat(timespec="seconds")
MINT="A"*44

def reports(mark_age=1,liq=30000,position_age=1,quote_status="QUOTE_AVAILABLE"):
    health={
      "checked_at_utc":STAMP,"status":"AANDACHT",
      "open_positions":[{"strategy":"A","token":MINT,"pair":"p",
        "last_market_age_minutes":mark_age,
        "market_liquidity_usd":liq,"age_hours":position_age}]}
    quotes={"checked_utc":STAMP,"status":"OK",
       "positions":[{"mint":MINT,"positions":[{"strategy":"A","status":quote_status}],
                     "warnings":[]}]}
    sec={"checked_utc":STAMP,"all_live_trades_blocked":True,
         "tokens":[{"mint":MINT,"security_gate":"BASIC_CHECKS_CLEAR"}]}
    exe={"checked_utc":STAMP,"live_orders_enabled":False,"positions":[]}
    return health,quotes,sec,exe

class RiskTests(unittest.TestCase):
    def test_stale_and_illiquid_are_blocked(self):
        h,q,s,e=reports(mark_age=300,liq=0,position_age=72)
        result=assess(h,q,s,e,NOW)
        reasons=result["positions"][0]["reasons"]
        self.assertIn("STALE_OR_UNKNOWN_MARK",reasons)
        self.assertIn("LOW_OR_UNKNOWN_LIQUIDITY",reasons)
        self.assertIn("POSITION_AGE_EXCEEDS_RESEARCH_HORIZON",reasons)
        self.assertEqual(result["positions_blocked"],1)
        self.assertEqual(result["positions"][0]["live_trade_permission"],"BLOCK")

    def test_quote_429_is_reported_but_not_interpreted_as_sellability(self):
        h,q,s,e=reports(quote_status="HTTP_429")
        result=assess(h,q,s,e,NOW)
        self.assertEqual(result["rate_limit_signals"],["HTTP_429"])
        self.assertIn("PAPER_EXIT_QUOTE_MISSING",result["positions"][0]["reasons"])

    def test_security_and_old_report_fail_closed(self):
        h,q,s,e=reports()
        s["tokens"]=[]
        q["checked_utc"]=(NOW-timedelta(minutes=16)).isoformat()
        result=assess(h,q,s,e,NOW)
        self.assertIn("QUOTE_REPORT_STALE_OR_MISSING",result["report_risks"])
        self.assertIn("TOKEN_SECURITY_UNVERIFIED",result["positions"][0]["reasons"])
        self.assertEqual(result["status"],"BLOCKED")

    def test_even_clear_evidence_never_authorizes_trade(self):
        result=assess(*reports(),NOW)
        self.assertEqual(result["positions_blocked"],0)
        self.assertEqual(result["positions"][0]["live_trade_permission"],"BLOCK")
        self.assertEqual(result["positions"][0]["paper_entry_permission"],"BLOCK")
        self.assertEqual(result["status"],"UNVERIFIED_NO_APPROVAL")

    def test_bad_timestamp_is_stale(self):
        self.assertTrue(old(NOW,"invalid",15))
        self.assertTrue(old(NOW,(NOW+timedelta(minutes=1)).isoformat(),15))

if __name__=="__main__":unittest.main()
