import unittest
from datetime import datetime,timedelta,timezone
from research_preentry_evidence_v31 import evaluate

NOW=datetime(2026,11,18,20,0,tzinfo=timezone.utc)
TS=NOW.isoformat(timespec="seconds")
MINT="A"*44
PAIR="B"*44

def samples():
    candidate={"mint":MINT,"pair":PAIR,"scanned_at_utc":TS,
      "liquidity_usd":"30000","volume_5m_usd":"1000",
      "buys_m5":15,"sells_m5":5,"pair_age_hours":1}
    route={"mint":MINT,"discovery_pair":PAIR,"checked_utc":TS,
      "buy_quote_only":True,"sell_quote_only":True,"buy_status":"OK",
      "sell_status":"OK","stake_usdc":"10","immediate_sell_usdc":"9.8"}
    security={"mint":MINT,"checked_utc":TS,"program":"SPL_TOKEN",
      "mint_authority":None,"freeze_authority":None,
      "extensions":[],"security_gate":"BASIC_CHECKS_CLEAR"}
    return candidate,route,security

class Tests(unittest.TestCase):
    def test_complete_fresh_evidence_only_research(self):
        a,b,c=samples()
        result=evaluate(a,b,c,NOW)
        self.assertEqual(result["evidence_state"],"PASS_RESEARCH_ONLY")
        self.assertEqual(result["paper_entry_permission"],"BLOCK")
        self.assertEqual(result["live_trade_permission"],"BLOCK")
        self.assertFalse(result["execution_verified"])

    def test_stale_low_liquidity_missing_sell_and_rate_limit(self):
        a,b,c=samples()
        a["scanned_at_utc"]=(NOW-timedelta(minutes=5)).isoformat()
        a["liquidity_usd"]="1000"
        b["sell_status"]="HTTP_429"
        b["immediate_sell_usdc"]=None
        result=evaluate(a,b,c,NOW)
        for code in ("MARK_STALE_OR_UNKNOWN","LOW_OR_UNKNOWN_LIQUIDITY",
                     "BUY_SELL_ROUTE_UNAVAILABLE","REVERSAL_QUOTE_INVALID"):
            self.assertIn(code,result["reasons"])
        self.assertEqual(result["evidence_state"],"BLOCKED")

    def test_wrong_pair_and_security_missing(self):
        a,b,c=samples()
        b["discovery_pair"]="OTHER"
        c.pop("freeze_authority")
        result=evaluate(a,b,c,NOW)
        self.assertIn("DISCOVERY_PAIR_EVIDENCE_MISMATCH",result["reasons"])
        self.assertIn("FREEZE_AUTHORITY_NOT_DISABLED_OR_UNKNOWN",result["reasons"])

    def test_future_and_unknown_block(self):
        a,b,c=samples()
        b["checked_utc"]=(NOW+timedelta(seconds=3)).isoformat()
        c["extensions"]=None
        a["buys_m5"]=None
        result=evaluate(a,b,c,NOW)
        for code in ("QUOTE_EVIDENCE_STALE","TOKEN_EXTENSIONS_UNKNOWN","BUY_PRESSURE_NOT_VERIFIED"):
            self.assertIn(code,result["reasons"])

    def test_spread_cost_and_stake_guard(self):
        a,b,c=samples()
        b["immediate_sell_usdc"]="9.6"
        b["stake_usdc"]="30"
        result=evaluate(a,b,c,NOW)
        self.assertIn("STAKE_INVALID",result["reasons"])
        self.assertIn("ROUNDTRIP_COST_EXCESSIVE",result["reasons"])

    def test_missing_all_evidence(self):
        a,_,_=samples()
        result=evaluate(a,None,None,NOW)
        self.assertEqual(result["evidence_state"],"BLOCKED")
        self.assertIn("SECURITY_EVIDENCE_MISSING_OR_WRONG_MINT",result["reasons"])
        self.assertIn("QUOTE_EVIDENCE_MISSING_OR_WRONG_MINT",result["reasons"])

if __name__=="__main__":unittest.main()
