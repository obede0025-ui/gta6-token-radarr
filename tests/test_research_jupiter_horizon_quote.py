"""Regression: one quote after the 15-minute research horizon, no fills."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from research_jupiter_minute_quotes import initialize, advance, USDC

BASE=datetime(2026,11,12,16,0,tzinfo=timezone.utc)
def at(seconds):return (BASE+timedelta(seconds=seconds)).isoformat()

class HorizonQuoteTests(unittest.TestCase):
    def test_single_delayed_quote_after_horizon(self):
        m1={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}}
        state=initialize(BASE,m1)
        state["positions"]["x"]={
            "mint":"exampleMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[
                {"quoted_utc":at(850),"status":"QUOTE_AVAILABLE",
                 "estimated_roundtrip_pct":1.0}
            ]}
        answer={"status":"QUOTE_AVAILABLE","out_raw":24500000,"router":"test"}
        with patch("research_jupiter_minute_quotes.get_quote",return_value=answer) as getter:
            advance(state,m1,None,BASE+timedelta(seconds=950),sleep_fn=lambda _:None)
            self.assertEqual(getter.call_count,1)
            self.assertEqual(len(state["positions"]["x"]["exit_quotes"]),2)
            self.assertEqual(state["positions"]["x"]["exit_quotes"][-1]["estimated_roundtrip_pct"],-2.0)
            advance(state,m1,None,BASE+timedelta(seconds=1000),sleep_fn=lambda _:None)
            self.assertEqual(getter.call_count,1)
            self.assertEqual(len(state["positions"]["x"]["exit_quotes"]),2)

    def test_three_http_400_errors_trigger_per_token_cooldown(self):
        m1={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}}
        state=initialize(BASE,m1)
        state["positions"]["bad"]={
            "mint":"badMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[
                {"quoted_utc":at(60),"status":"QUOTE_HTTP_ERROR","http_status":400},
                {"quoted_utc":at(120),"status":"QUOTE_HTTP_ERROR","http_status":400},
                {"quoted_utc":at(180),"status":"QUOTE_HTTP_ERROR","http_status":400}
            ]}
        state["positions"]["good"]={
            "mint":"goodMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[]}
        with patch("research_jupiter_minute_quotes.get_quote",return_value={
            "status":"QUOTE_AVAILABLE","out_raw":24500000}) as getter:
            advance(state,m1,None,BASE+timedelta(seconds=240),sleep_fn=lambda _:None)
            self.assertEqual(getter.call_count,1)
            self.assertEqual(getter.call_args.args[1],"goodMint")
            self.assertEqual(len(state["positions"]["bad"]["exit_quotes"]),3)
            self.assertEqual(state["positions"]["bad"]["sell_quote_research_status"],
                             "SELL_QUOTE_UNAVAILABLE_RESEARCH_ONLY")
            advance(state,m1,None,BASE+timedelta(seconds=500),sleep_fn=lambda _:None)
            self.assertEqual(getter.call_count,2)
            self.assertEqual(getter.call_args.args[1],"badMint")
            self.assertEqual(state["positions"]["bad"]["sell_quote_research_status"],
                             "SELL_QUOTE_OBSERVED_RESEARCH_ONLY")

    def test_mixed_error_breaks_consecutive_http_400_streak(self):
        from research_jupiter_minute_quotes import consecutive_http_400
        self.assertEqual(consecutive_http_400([
            {"status":"QUOTE_HTTP_ERROR","http_status":400},
            {"status":"QUOTE_AVAILABLE"},
            {"status":"QUOTE_HTTP_ERROR","http_status":400}
        ]),1)

    def test_http_failure_details_exclude_untrusted_response_body(self):
        from research_jupiter_minute_quotes import get_quote
        class Response:
            status_code=400
            def json(self):
                return {"errorCode":"NO_ROUTE","errorMessage":"No route found",
                        "secret":"must not be included"}
        class Session:
            def get(self,*args,**kwargs):
                return Response()
        result=get_quote(Session(),"someToken","USDC",123,"private-key")
        self.assertEqual(result["status"],"QUOTE_HTTP_ERROR")
        self.assertEqual(result["error_code"],"NO_ROUTE")
        self.assertEqual(result["error_message"],"No route found")
        self.assertNotIn("secret",result)
        self.assertNotIn("private-key",str(result))

    def test_beyond_grace_period_no_quote(self):
        m1={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}}
        state=initialize(BASE,m1)
        state["positions"]["x"]={
            "mint":"exampleMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[]}
        with patch("research_jupiter_minute_quotes.get_quote") as getter:
            advance(state,m1,None,BASE+timedelta(seconds=1021),sleep_fn=lambda _:None)
            getter.assert_not_called()


    def test_quote_request_timing_is_recorded(self):
        m1={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}}
        state=initialize(BASE,m1)
        state["positions"]["x"]={
            "mint":"exampleMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[]}
        with patch("research_jupiter_minute_quotes.get_quote",return_value={
            "status":"QUOTE_AVAILABLE","out_raw":24500000}):
            advance(state,m1,None,BASE+timedelta(seconds=60),sleep_fn=lambda _:None)
        q=state["positions"]["x"]["exit_quotes"][0]
        self.assertIsNotNone(q.get("request_started_utc"))
        self.assertIsNotNone(q.get("response_received_utc"))
        self.assertGreaterEqual(q.get("response_latency_ms"),0)

if __name__=="__main__":unittest.main()
