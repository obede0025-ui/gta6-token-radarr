"""Tests for opt-in V3.2 pre-entry roundtrip quote gate (no network)."""
import unittest
from datetime import datetime, timedelta, timezone

from m1_v3_2_forward_quotes import bootstrap, evaluate, USDC

MINT = "So11111111111111111111111111111111111111112"

class PreentryGateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 15, tzinfo=timezone.utc)
        self.m1 = {"version": 1, "tracked": {MINT: {
            "mint": MINT,
            "signal": (self.now - timedelta(seconds=30)).isoformat(),
            "status": "WATCHING",
        }}}
        self.state = bootstrap({"version": 1, "tracked": {}}, self.now)
        self.calls = []

    def run_quote(self, proceeds=24750000, fail_sell=False):
        def mock(src, dst, amount):
            self.calls.append((src, dst, amount))
            if src == USDC:
                return {"output_raw": 1000000, "quote_only": True, "error_code": None}
            if fail_sell:
                raise ValueError("No sell route")
            return {"output_raw": proceeds, "quote_only": True, "error_code": None}
        return evaluate(self.state, self.m1, self.now, mock, preentry_gate=True)

    def test_passes_and_reuses_roundtrip_sell_quote(self):
        result = self.run_quote()
        p = self.state["positions"][MINT]
        self.assertEqual(result["jupiter_requests"], 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1], (MINT, USDC, 1000000))
        self.assertEqual(p["status"], "ACTIVE")
        self.assertEqual(p["preentry_gate"], "PASS")
        self.assertEqual(len(p["exit_quotes"]), 1)
        self.assertEqual(p["entry_token_raw"], "1000000")

    def test_exact_minus_three_percent_passes(self):
        self.run_quote(proceeds=24250000)
        self.assertEqual(self.state["positions"][MINT]["status"], "ACTIVE")

    def test_more_than_three_percent_blocks(self):
        self.run_quote(proceeds=23500000)
        p = self.state["positions"][MINT]
        self.assertEqual(p["status"], "ENTRY_BLOCKED_ROUNDTRIP")
        self.assertEqual(p["preentry_gate"], "BLOCK")
        self.assertIsNone(p["entry_token_raw"])
        self.assertIsNone(p["entry_quote_utc"])

    def test_missing_sell_route_fails_closed(self):
        self.run_quote(fail_sell=True)
        p = self.state["positions"][MINT]
        self.assertEqual(p["status"], "ENTRY_BLOCKED_NO_ROUTE")
        self.assertIsNone(p["entry_quote_utc"])

    def test_invalid_quote_response_fails_closed(self):
        def mock(src, dst, amount):
            return {"output_raw": 1000000, "quote_only": False, "error_code": None}
        evaluate(self.state, self.m1, self.now, mock, preentry_gate=True)
        self.assertEqual(self.state["positions"][MINT]["status"], "ENTRY_BLOCKED_NO_ROUTE")

    def test_default_behavior_is_unchanged(self):
        def legacy(src, dst, amount):
            self.calls.append((src, dst, amount))
            return {"output_raw": 1000000 if src == USDC else 24000000}
        result = evaluate(self.state, self.m1, self.now, legacy)
        self.assertEqual(result["jupiter_requests"], 2)
        self.assertEqual(self.state["positions"][MINT]["status"], "ACTIVE")
        self.assertNotIn("preentry_roundtrip_pct", self.state["positions"][MINT])

if __name__ == "__main__":
    unittest.main()
