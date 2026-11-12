import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from research_jupiter_minute_quotes import (
    initialize,advance,validate_response,USDC,STAKE_RAW
)
BASE=datetime(2026,11,12,16,0,tzinfo=timezone.utc)
def ts(m):return (BASE+timedelta(minutes=m)).isoformat()
def fake(ident="id",signal=1,reg=1.3):
    return {"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{ident:{
        "mint":"MintExample0123456789","pair":"pair",
        "signal_utc":ts(signal),"registered_utc":ts(reg),"research_only":True}}}
class MockSession:pass

class QuoteTests(unittest.TestCase):
    def test_quote_only_no_transaction_and_amount_guard(self):
        good={"transaction":None,"inAmount":str(STAKE_RAW),
              "outAmount":"1234567890","router":"metis"}
        self.assertEqual(validate_response(good,USDC,"mint",STAKE_RAW)["status"],"QUOTE_AVAILABLE")
        self.assertEqual(validate_response({**good,"transaction":"base64"},USDC,"mint",STAKE_RAW)["status"],"QUOTE_NOT_QUOTE_ONLY")
        self.assertEqual(validate_response({**good,"inAmount":"10"},USDC,"mint",STAKE_RAW)["status"],"QUOTE_INVALID_AMOUNTS")

    def test_forward_entries_and_reverse_quoted_same_raw_amount(self):
        state=initialize(BASE,{"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}})
        calls=[]
        def mock(session,a,b,amount,key):
            calls.append((a,b,amount))
            if a==USDC:return {"status":"QUOTE_AVAILABLE","out_raw":500_000_000,"router":"metis"}
            return {"status":"QUOTE_AVAILABLE","out_raw":24_000_000,"router":"metis"}
        with patch("research_jupiter_minute_quotes.get_quote",mock):
            result=advance(state,fake(),MockSession(),BASE+timedelta(minutes=2),sleep_fn=lambda _:None)
        self.assertEqual(result["new_quote_candidates"],1)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][2],500_000_000)
        self.assertEqual(calls[0][0],USDC)
        self.assertEqual(state["positions"]["id"]["exit_quotes"][0]["estimated_roundtrip_pct"],-4)

    def test_no_old_candidate_replay(self):
        state=initialize(BASE+timedelta(minutes=5),{"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}})
        result=advance(state,fake(),MockSession(),BASE+timedelta(minutes=6),sleep_fn=lambda _:None)
        self.assertEqual(result["requests_used"],0)
        self.assertEqual(result["positions"],0)

    def test_budget_and_max_active(self):
        state=initialize(BASE,{"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}})
        source={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{
            str(i):{"mint":"M"+str(i),"pair":"P","signal_utc":ts(1),
                    "registered_utc":ts(1.3),"research_only":True} for i in range(4)}}
        with patch("research_jupiter_minute_quotes.get_quote",
                   side_effect=lambda *args:{"status":"QUOTE_AVAILABLE","out_raw":1000,"router":"metis"}):
            result=advance(state,source,MockSession(),BASE+timedelta(minutes=2),sleep_fn=lambda _:None)
        self.assertEqual(result["requests_used"],4)
        self.assertEqual(result["positions"],2)

    def test_api_request_only_get_and_never_taker(self):
        from research_jupiter_minute_quotes import get_quote
        class Response:
            status_code=200
            def json(self):return {"transaction":None,"inAmount":str(STAKE_RAW),"outAmount":"500"}
        class Session:
            def get(self,url,params,headers,timeout):
                self.params=params
                return Response()
        s=Session()
        self.assertEqual(get_quote(s,USDC,"mint",STAKE_RAW,"")["status"],"QUOTE_AVAILABLE")
        self.assertNotIn("taker",s.params)

if __name__=="__main__":unittest.main()
