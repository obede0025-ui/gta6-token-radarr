"""Deterministic offline integration checks for 5m/15m quote tracking.

No Jupiter/RPC calls, no original DB/portfolio mutation, no real trades.
"""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from research_quote_cohort_v33 import new_state,resolve_followups
from research_observer_health_v34 import metrics

START=datetime(2026,11,18,21,58,19,tzinfo=timezone.utc)
MINT="A"*44
PAIR="B"*44
KEY=MINT+"|"+PAIR

class FrozenClock:
    now=START
    @classmethod
    def at(cls,timezone_arg=None):
        return cls.now

class FakeQuoter:
    def __init__(self,outputs=()):
        self.outputs=list(outputs);self.calls=[]
    def quote(self,a,b,amount):
        self.calls.append((a,b,amount))
        x=self.outputs.pop(0)
        if isinstance(x,Exception):raise x
        return x

def state():
    s=new_state(START)
    s["signals"][KEY]={
        "mint":MINT,"pair":PAIR,"strategy":"B",
        "first_seen_utc":START.isoformat(timespec="seconds"),
        "source_scanned_at_utc":START.isoformat(timespec="seconds"),
        "buy_raw_out":"5000000","followups":{},
        "security_status":"BLOCK","evidence_state":"BLOCKED",
        "paper_trade_approval":"BLOCK","live_trade_approval":"BLOCK"}
    return s

class QuoteHorizonTests(unittest.TestCase):
    def test_realistic_five_and_fifteen_minute_followups(self):
        s=state();q=FakeQuoter([9200000,11200000])
        with patch("research_quote_cohort_v33.datetime",FrozenClock):
            FrozenClock.now=START+timedelta(minutes=4,seconds=55)
            self.assertEqual(resolve_followups(s,FrozenClock.now,q),[])
            FrozenClock.now=START+timedelta(minutes=5,seconds=25)
            out=resolve_followups(s,FrozenClock.now,q)
            self.assertEqual([x[1] for x in out],["300"])
            self.assertEqual(s["signals"][KEY]["followups"]["300"]["status"],
                             "JUPITER_SELL_ROUTE_QUOTED")
            self.assertEqual(s["signals"][KEY]["followups"]["300"]["quote_return_pct_before_network_fees"],"-8.00")
            self.assertEqual(resolve_followups(s,FrozenClock.now,q),[])
            FrozenClock.now=START+timedelta(minutes=15,seconds=28)
            out=resolve_followups(s,FrozenClock.now,q)
            self.assertEqual([x[1] for x in out],["900"])
            self.assertEqual(s["signals"][KEY]["followups"]["900"]["quote_return_pct_before_network_fees"],"12.00")
        self.assertEqual(len(q.calls),2)
        self.assertTrue(all(c[2]==5000000 for c in q.calls))

    def test_late_horizon_is_missed_and_not_backfilled(self):
        s=state();q=FakeQuoter([])
        with patch("research_quote_cohort_v33.datetime",FrozenClock):
            FrozenClock.now=START+timedelta(minutes=8)
            results=resolve_followups(s,FrozenClock.now,q)
            self.assertEqual(results,[(KEY,"300","HORIZON_MISSED")])
        self.assertEqual(len(q.calls),0)
        self.assertIsNone(s["signals"][KEY]["followups"]["300"]["quote_return_pct_before_network_fees"])

    def test_network_unavailable_does_not_become_profit(self):
        s=state();q=FakeQuoter([ValueError("no quote")])
        with patch("research_quote_cohort_v33.datetime",FrozenClock):
            FrozenClock.now=START+timedelta(minutes=5,seconds=5)
            result=resolve_followups(s,FrozenClock.now,q)
        self.assertEqual(result[0][2],"SELL_ROUTE_UNAVAILABLE")
        health=metrics(s,{"checked_utc":FrozenClock.now.isoformat()},FrozenClock.now)
        self.assertEqual(health["sell_unavailable"],1)
        self.assertIsNone(health["quote_return_average_pct_observed_only"])

    def test_no_preentry_returns_are_counted_as_exec_pnl(self):
        s=state()
        h=metrics(s,{"checked_utc":START.isoformat()},START)
        self.assertEqual(h["quote_samples"],0)
        self.assertEqual(h["trades_executed"],0)
        self.assertFalse(h["sample_sufficient"])

if __name__=="__main__":unittest.main()
