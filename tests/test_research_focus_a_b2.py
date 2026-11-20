import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_focus_a_b2 import analyse, paired_comparison, signal_labels

M1 = "9rbKkz2BDQBVgZmrHY6d7PTq2ge91gkpEyE4y9PPpump"
M2 = "5Jf1Y8jDxrzdXeA3jDZN7rqrzwnbKJadksg4Xvmj4rp8"
NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db, self.abc, self.b2 = [root / p for p in ("db.sqlite", "abc.json", "b2.json")]
        self.abc.write_text(json.dumps({"closed": {"A": [
            {"token": M1, "pnl_usd": 5}, {"token": "other", "pnl_usd": -2}]},
            "open": {"A": {M2: {"quantity": 10}}}}))
        self.b2.write_text(json.dumps({"closed": [
            {"token": M1, "pnl_usd": 1}], "open": {M2: {"quantity": 1}}}))
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE snapshots(
                scanned_at TEXT,token_address TEXT,pair_address TEXT,
                price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            self.add(conn, M1, NOW-timedelta(minutes=11), 1, 500, 21000)
            self.add(conn, M1, NOW-timedelta(minutes=6), 1.03, 700, 22000)
            self.add(conn, M1, NOW-timedelta(seconds=30), 1.10, 900, 25000)

    def add(self, conn, mint, when, price, volume, liq):
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)", (
            when.isoformat(), mint, "pair1", price, liq, volume, 12, 4, 1))

    def test_closed_only_and_full_at_risk(self):
        c = paired_comparison(
            json.loads(self.abc.read_text()), json.loads(self.b2.read_text()))
        self.assertEqual(c["strategy_a"]["closed_pnl_usd"], 3)
        self.assertEqual(c["strategy_a"]["zero_recovery_stress_pnl_usd"], -22)
        self.assertEqual(c["strategy_b2"]["zero_recovery_stress_pnl_usd"], -24)
        self.assertEqual(c["shared_exposed_mints"], 2)
        self.assertEqual(c["winner_after_all_open_exits"], "UNPROVEN")

    def test_momentum_labels(self):
        h = [(NOW, 1., 500.), (NOW, 1.03, 700.)]
        self.assertEqual(signal_labels(h, 1.10, 900), ["A", "B2_SIGNAL_PROXY"])
        self.assertEqual(signal_labels(h, 1.02, 900), [])

    def test_read_only_report_selects_fresh_signal(self):
        before_db = self.db.read_bytes()
        before_a = self.abc.read_bytes()
        report = analyse(self.db, self.abc, self.b2, now=NOW)
        self.assertEqual(report["historical_coverage"]["snapshot_rows"], 3)
        self.assertEqual(report["historical_coverage"]["market_quality_pass_observations"], 3)
        self.assertEqual(report["historical_coverage"]["a_signal_proxy_observations"], 1)
        self.assertEqual(report["historical_coverage"]["b2_signal_proxy_observations"], 1)
        self.assertEqual(report["fresh_shortlist"][0]["mint"], M1)
        self.assertEqual(report["fresh_shortlist"][0]["strategies"], ["A", "B2_SIGNAL_PROXY"])
        self.assertEqual(report["fresh_shortlist"][0]["live_trade_approval"], "BLOCK")
        self.assertEqual(report["quote_calls"], 0)
        self.assertEqual(self.db.read_bytes(), before_db)
        self.assertEqual(self.abc.read_bytes(), before_a)

    def test_old_momentum_not_replayed_as_fresh(self):
        report = analyse(self.db, self.abc, self.b2, now=NOW+timedelta(minutes=10))
        self.assertEqual(report["fresh_shortlist"], [])
        self.assertEqual(report["shortlist_status"], "NO_FRESH_MATCHING_SIGNALS")

    def test_no_history_spanning_long_gaps(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM snapshots")
            self.add(conn, M1, NOW-timedelta(minutes=31), 1, 500, 21000)
            self.add(conn, M1, NOW-timedelta(minutes=25), 1.03, 700, 22000)
            self.add(conn, M1, NOW-timedelta(seconds=30), 1.10, 900, 25000)
        r = analyse(self.db, self.abc, self.b2, now=NOW)
        self.assertEqual(r["historical_coverage"]["gaps_over_20min"], 1)
        self.assertEqual(r["fresh_shortlist"], [])

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            analyse(self.db, self.abc, self.b2, now=datetime(2026,11,20))
        with self.assertRaises(ValueError):
            analyse(self.db, self.abc, self.b2, now=NOW, hours=999)


if __name__ == "__main__":
    unittest.main()
