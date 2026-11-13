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

    def test_beyond_grace_period_no_quote(self):
        m1={"mode":"M1_MINUTE_RESEARCH_ONLY","candidates":{}}
        state=initialize(BASE,m1)
        state["positions"]["x"]={
            "mint":"exampleMint","signal_utc":at(-60),"entry_quote_utc":at(0),
            "entry_token_raw":1000000,"exit_quotes":[]}
        with patch("research_jupiter_minute_quotes.get_quote") as getter:
            advance(state,m1,None,BASE+timedelta(seconds=1021),sleep_fn=lambda _:None)
            getter.assert_not_called()

if __name__=="__main__":unittest.main()
