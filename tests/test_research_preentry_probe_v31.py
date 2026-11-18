import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from research_preentry_probe_v31 import probe_candidate,KeylessQuoteOnly
from radar_token_security import LEGACY
from research_discovery_quote_v27 import USDC

NOW=datetime(2026,11,18,20,0,tzinfo=timezone.utc)
MINT="A"*44
PAIR="B"*44
C={"mint":MINT,"pair":PAIR,"strategy":"B",
   "scanned_at_utc":NOW.isoformat(timespec="seconds"),
   "liquidity_usd":"30000","volume_5m_usd":"1000",
   "buys_m5":10,"sells_m5":3,"pair_age_hours":1}

def account():
    return {"owner":LEGACY,"data":{"parsed":{"type":"mint",
        "info":{"mintAuthority":None,"freezeAuthority":None,
                "decimals":6,"supply":"12345"}}}}

class FakeQuoter:
    def __init__(self,fail_sell=False):self.fail_sell=fail_sell;self.calls=[]
    def quote(self,a,b,raw):
        self.calls.append((a,b,raw))
        if a!=USDC and self.fail_sell:raise ValueError("no sell route")
        return 100000 if a==USDC else 9800000

class Tests(unittest.TestCase):
    def test_prospective_fresh_quote_security_is_still_paper_block(self):
        q=FakeQuoter()
        with patch("research_preentry_probe_v31.rpc_get_account",return_value=account()):
            verdict,route,security=probe_candidate(C,q,object(),"https://rpc.example",lambda:NOW)
        self.assertEqual(verdict["evidence_state"],"PASS_RESEARCH_ONLY")
        self.assertEqual(verdict["live_trade_permission"],"BLOCK")
        self.assertEqual(verdict["paper_entry_permission"],"BLOCK")
        self.assertEqual((route["buy_status"],route["sell_status"]),("OK","OK"))
        self.assertEqual(len(q.calls),2)

    def test_missing_sell_route_and_authority_fail_closed(self):
        bad=account()
        bad["data"]["parsed"]["info"]["freezeAuthority"]="A"*44
        with patch("research_preentry_probe_v31.rpc_get_account",return_value=bad):
            verdict,route,sec=probe_candidate(C,FakeQuoter(fail_sell=True),object(),"https://rpc.example",lambda:NOW)
        self.assertEqual(verdict["evidence_state"],"BLOCKED")
        self.assertIn("BUY_SELL_ROUTE_UNAVAILABLE",verdict["reasons"])
        self.assertIn("FREEZE_AUTHORITY_NOT_DISABLED_OR_UNKNOWN",verdict["reasons"])

    def test_rpc_missing_fail_closed(self):
        with patch("research_preentry_probe_v31.rpc_get_account",side_effect=ValueError("rpc")):
            verdict,route,sec=probe_candidate(C,FakeQuoter(),object(),"https://rpc.example",lambda:NOW)
        self.assertEqual(verdict["evidence_state"],"BLOCKED")
        self.assertEqual(sec["security_gate"],"BLOCK")

    def test_quote_pacer_get_only(self):
        class Resp:
            def json(self):return {"outAmount":"999","inputMint":USDC,"outputMint":MINT}
        class Pacer:
            def get(self,session,url,**kwargs):
                self.kwargs=kwargs;return Resp()
        fake=Pacer()
        with patch("research_preentry_probe_v31.SHARED_PACER",fake):
            q=KeylessQuoteOnly()
            try:self.assertEqual(q.quote(USDC,MINT,10),999)
            finally:q.close()
        self.assertEqual(fake.kwargs["params"]["amount"],"10")
        self.assertNotIn("taker",fake.kwargs["params"])
        self.assertEqual(fake.kwargs["timeout"],15)

if __name__=="__main__":unittest.main()
