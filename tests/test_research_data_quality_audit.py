"""Offline tests for research_data_quality_audit.py; no VPS access needed."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_data_quality_audit import audit, parse_timestamp


class DataQualityAuditTests(unittest.TestCase):
    def test_timestamp_must_be_timezone_aware(self):
        self.assertIsNone(parse_timestamp("2026-11-20T10:00:00"))
        self.assertIsNotNone(parse_timestamp("2026-11-20T10:00:00Z"))

    def test_initial_bad_data_not_replaced_by_later_valid_data(self):
        with tempfile.TemporaryDirectory() as folder:
            learning = Path(folder) / "learning.db"
            minute = Path(folder) / "minute.sqlite"
            with sqlite3.connect(learning) as db:
                db.execute("""CREATE TABLE snapshots (
                    token_address TEXT, pair_address TEXT, scanned_at TEXT,
                    price_usd REAL, liquidity_usd REAL,
                    pair_age_hours REAL, source TEXT)""")
                db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", [
                    ("A", "P", "2026-11-20T10:00:00Z", None, 5000, 0.1, "search_new"),
                    ("A", "P", "2026-11-20T10:05:00Z", 1, 10000, 0.2, "early_follow"),
                    ("B", "Q", "2026-11-20T10:00:00Z", 2, 20000, 0.5, "profile_new")
                ])
            with sqlite3.connect(minute) as db:
                db.execute("""CREATE TABLE minute_observations (
                    observed_at TEXT, mint TEXT, pair TEXT, status TEXT,
                    price_usd REAL, liquidity_usd REAL)""")
                db.executemany("INSERT INTO minute_observations VALUES (?,?,?,?,?,?)", [
                    ("2026-11-20T10:00:00Z", "A", "P", "QUOTE_MARKET_OBSERVED", 1, 20000),
                    ("2026-11-20T10:01:02Z", "A", "P", "QUOTE_MARKET_OBSERVED", 2, 21000),
                    ("2026-11-20T10:01:02Z", "B", "Q", "LIQUIDITY_UNKNOWN", 2, None),
                    ("2026-11-20T10:02:02Z", "B", "Q", "MARKET_DATA_UNAVAILABLE", None, None),
                    ("2026-11-20T10:03:02Z", "B", "Q", "QUOTE_MARKET_OBSERVED", 2, 0)
                ])
            result = audit(learning, minute)
            self.assertEqual(result["learning"]["unique_tokens"], 2)
            self.assertEqual(result["learning"]["valid_initial_market_data"], 1)
            self.assertEqual(result["learning"]["issue_counts_overlapping"]["invalid_initial_price"], 1)
            self.assertEqual(result["minute_market"]["observations"], 3)
            self.assertEqual(result["minute_market"]["tokens_in_both_databases"], 2)
            self.assertEqual(result["minute_market"]["median_pair_observation_gap_seconds"], 62)
            breakdown = {
                row["status"]: row
                for row in result["minute_market"]["status_breakdown_all_observations"]
            }
            self.assertEqual(breakdown["QUOTE_MARKET_OBSERVED"]["liquidity_zero"], 1)
            self.assertEqual(breakdown["LIQUIDITY_UNKNOWN"]["liquidity_null"], 1)
            self.assertEqual(breakdown["MARKET_DATA_UNAVAILABLE"]["price_null"], 1)
            self.assertEqual(sum(r["observations"] for r in breakdown.values()), 5)
            self.assertEqual(result["database_writes"], 0)


if __name__ == "__main__":
    unittest.main()
