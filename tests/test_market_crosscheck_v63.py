import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from market_crosscheck_v63 import compare

class CrosscheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "market.db"
        self.now = datetime(2026, 11, 8, 20, 30, tzinfo=timezone.utc)
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE snapshots (token_address TEXT, scanned_at TEXT, price_usd REAL, liquidity_usd REAL, pair_address TEXT)")
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                         ("testmint", self.now.isoformat(), 0.000001, 1600, "pair1"))
        self.quote = {"checked_utc": self.now.isoformat(), "positions": [
            {"mint": "testmint", "positions": [
                {"strategy": "B", "status": "QUOTE_AVAILABLE",
                 "position_quantity": "125000", "estimated_exit_usdc": "0.125"}]}]}
    def tearDown(self):
        self.temp.cleanup()
    def test_low_liquidity_blocks_even_with_matching_price(self):
        p = compare(self.quote, self.db, now=self.now)["positions"][0]
        self.assertEqual(p["reason"], "LOW_OR_UNKNOWN_LIQUIDITY")
        self.assertEqual(p["risk"], "BLOCK_FOR_REVIEW")
    def test_price_gap(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE snapshots SET liquidity_usd=20000")
        self.quote["positions"][0]["positions"][0]["estimated_exit_usdc"] = "0.002"
        p = compare(self.quote, self.db, now=self.now)["positions"][0]
        self.assertEqual(p["reason"], "PRICE_DISAGREEMENT")
    def test_consistent_not_trading_authorization(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE snapshots SET liquidity_usd=20000")
        p = compare(self.quote, self.db, now=self.now)["positions"][0]
        self.assertEqual(p["risk"], "PASS_RESEARCH_ONLY")
    def test_stale_quote_unknown(self):
        p = compare(self.quote, self.db, now=self.now + timedelta(hours=1))["positions"][0]
        self.assertEqual((p["risk"], p["reason"]), ("UNKNOWN", "STALE_QUOTE"))
    def test_missing_snapshot_unknown(self):
        self.quote["positions"][0]["mint"] = "absent"
        p = compare(self.quote, self.db, now=self.now)["positions"][0]
        self.assertEqual(p["reason"], "NO_SNAPSHOT")

if __name__ == "__main__":
    unittest.main()
