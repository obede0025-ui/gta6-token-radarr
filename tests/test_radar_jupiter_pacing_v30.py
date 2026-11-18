import unittest
from unittest.mock import patch
from decimal import Decimal
import requests

from radar_jupiter_pacing_v30 import JupiterPacer, MAX_ATTEMPTS
from radar_quote_monitor import quote, USDC, compare_positions, sanitize_error

MINT="A"*44

class Clock:
    def __init__(self):self.value=0.0;self.sleeps=[]
    def now(self):return self.value
    def sleep(self,v):self.sleeps.append(v);self.value+=v

class Response:
    def __init__(self,status,amount="100",headers=None):
        self.status_code=status;self.headers=headers or {};self.amount=amount
    def raise_for_status(self):
        if self.status_code >= 400:
            exc=requests.HTTPError("error")
            exc.response=self
            raise exc
    def json(self):return {"outAmount":self.amount,"router":"metis"}

class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        r=self.responses.pop(0)
        if isinstance(r,Exception):raise r
        return r

class Tests(unittest.TestCase):
    def test_429_then_success_bounded(self):
        c=Clock();p=JupiterPacer(interval=2.5,clock=c.now,sleep=c.sleep)
        s=Session([Response(429,headers={"Retry-After":"3"}),Response(200)])
        with patch("radar_quote_monitor.SHARED_PACER",p):
            res=quote(s,"test-key",USDC,MINT,100)
        self.assertEqual(res["output_raw"],100)
        self.assertEqual(len(s.calls),2)
        self.assertEqual(p.rate_limits,1)
        self.assertEqual(p.total_retries,1)
        self.assertTrue(all(x<=12 for x in c.sleeps))
        self.assertTrue(all(call[1]["params"].get("taker") is None for call in s.calls))
        self.assertTrue(all(call[1]["timeout"]==15 for call in s.calls))

    def test_exhausted_rate_limit_fail_closed(self):
        c=Clock();p=JupiterPacer(clock=c.now,sleep=c.sleep)
        s=Session([Response(429,headers={"Retry-After":"999"}) for _ in range(MAX_ATTEMPTS)])
        with patch("radar_quote_monitor.SHARED_PACER",p):
            with self.assertRaises(requests.HTTPError) as cm:
                quote(s,"test-key",USDC,MINT,100)
        self.assertEqual(sanitize_error(cm.exception),"HTTP_429")
        self.assertEqual(len(s.calls),MAX_ATTEMPTS)
        self.assertLessEqual(max(c.sleeps),12)

    def test_5xx_and_timeouts(self):
        c=Clock();p=JupiterPacer(clock=c.now,sleep=c.sleep)
        s=Session([requests.Timeout("timeout"),Response(503),Response(200)])
        result=p.get(s,"https://api.jup.ag/swap/v2/order",timeout=15)
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(s.calls),3)

    def test_nonretryable_http_400(self):
        c=Clock();p=JupiterPacer(clock=c.now,sleep=c.sleep)
        s=Session([Response(400)])
        with self.assertRaises(requests.HTTPError):
            p.get(s,"https://api.jup.ag/swap/v2/order")
        self.assertEqual(len(s.calls),1)

    def test_paces_independent_requests_in_one_process(self):
        c=Clock();p=JupiterPacer(clock=c.now,sleep=c.sleep)
        s=Session([Response(200),Response(200)])
        p.get(s,"https://api.jup.ag/swap/v2/order")
        p.get(s,"https://api.jup.ag/swap/v2/order")
        self.assertGreaterEqual(sum(c.sleeps),2.5)

    def test_missing_decimals_are_explicit_and_not_exit_quote(self):
        state={"open":{"A":{MINT:{"opened_at":"2026-11-18T00:00:00+00:00",
               "entry_market":0.001,"quantity":100}}, "B":{},"C":{}}}
        session=Session([Response(200)])
        with patch("radar_quote_monitor.quote",side_effect=ValueError("no route")),\
             patch("radar_quote_monitor.get_decimals",side_effect=ValueError("no decimals")):
            out=compare_positions(state,session=session,api_key="x",
                rpc_url="https://rpc.invalid",stake_usdc=Decimal("25"))
        self.assertEqual(out[0]["positions"][0]["status"],"DECIMALS_UNAVAILABLE")
        self.assertIsNone(out[0]["positions"][0]["estimated_exit_usdc"])

if __name__=="__main__":unittest.main()
