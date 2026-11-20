"""Offline forward-only research quote tests; no HTTP, portfolio or DB writes."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_focus_forward_quotes import run

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
MINT = "9rbKkz2BDQBVgZmrHY6d7PTq2ge91gkpEyE4y9PPpump"


class FakeQuoter:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def quote(self, a, b, amount):
        self.calls.append((a,b,amount))
        if self.fail:
            raise ValueError("unavailable")
        return 1000000 if len(self.calls) == 1 else 9600000


class TestForward(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name)
        self.db, self.state = p/"market.sqlite", p/"data"/"focus.json"
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE snapshots(scanned_at TEXT,token_address TEXT,
                pair_address TEXT,price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            for minutes, price, vol in [(11,1.0,500),(6,1.03,700)]:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                    ((NOW-timedelta(minutes=minutes)).isoformat(),MINT,"P1",price,30000,vol,8,4,1))
            c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                ((NOW-timedelta(seconds=20)).isoformat(),MINT,"P1",1.1,30000,900,8,4,1))

    def test_forward_entry_and_one_five_fifteen_followups(self):
        before = self.db.read_bytes()
        q = FakeQuoter()
        result = run(self.db,self.state,now=NOW,clock=lambda:NOW,quoter=q)
        self.assertEqual(result["new_signals"],1)
        self.assertEqual(result["followups"],[])
        self.assertEqual(len(q.calls),2)
        self.assertEqual(self.db.read_bytes(),before)
        for sec in (60,300,900):
            t = NOW+timedelta(seconds=sec)
            report=run(self.db,self.state,now=t,clock=lambda:t,quoter=q)
            self.assertEqual(report["new_signals"],0)
            self.assertEqual([v["horizon"] for v in report["followups"]],[sec])
        s = list(json.loads(self.state.read_text())["signals"].values())[0]
        self.assertEqual(s["followups"]["60"]["status"],"SELL_QUOTE_ONLY")
        self.assertEqual(s["followups"]["900"]["status"],"SELL_QUOTE_ONLY")
        self.assertEqual(s["live_trade_approval"],"BLOCK")
        self.assertEqual(len(q.calls),5)

    def test_stale_signals_not_added(self):
        now=NOW+timedelta(minutes=4)
        q=FakeQuoter()
        r=run(self.db,self.state,now=now,clock=lambda:now,quoter=q)
        self.assertEqual(r["new_signals"],0)
        self.assertEqual(q.calls,[])

    def test_no_reconstruction_of_missed_horizons(self):
        q=FakeQuoter()
        run(self.db,self.state,now=NOW,clock=lambda:NOW,quoter=q)
        later=NOW+timedelta(minutes=20)
        report=run(self.db,self.state,now=later,clock=lambda:later,quoter=q)
        self.assertEqual(len(report["followups"]),3)
        s=list(json.loads(self.state.read_text())["signals"].values())[0]
        self.assertEqual(s["followups"]["60"]["status"],"MISSED_HORIZON")
        self.assertEqual(s["followups"]["900"]["status"],"MISSED_HORIZON")
        self.assertEqual(len(q.calls),2)

    def test_missing_entry_does_not_create_successful_exit(self):
        q=FakeQuoter(fail=True)
        r=run(self.db,self.state,now=NOW,clock=lambda:NOW,quoter=q)
        self.assertEqual(r["new_signals"],1)
        later=NOW+timedelta(seconds=60)
        run(self.db,self.state,now=later,clock=lambda:later,quoter=q)
        s=list(json.loads(self.state.read_text())["signals"].values())[0]
        self.assertEqual(s["entry_quote_status"],"NO_BUY_ROUTE")
        self.assertEqual(s["followups"]["60"]["status"],"NO_HYPOTHETICAL_ENTRY_AMOUNT")

    def test_state_cannot_overwrite_source_db(self):
        with self.assertRaises(ValueError):
            run(self.db,self.db,now=NOW,clock=lambda:NOW,quoter=FakeQuoter())


if __name__ == "__main__":
    unittest.main()
