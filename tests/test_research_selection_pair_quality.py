import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from research_selection_pair_quality import analyze, report

STAMP = "2026-11-14T12:00:00+00:00"


def sample(cohort, mint, status, stamp=STAMP, price=None, liq=None, vol=None, buys=None, sells=None, age=None):
    return (stamp, cohort, mint, "pair-" + mint, status, price, liq, vol, buys, sells, age)


class PairQualityTests(unittest.TestCase):
    def test_shared_counted_once_and_exclusive_unavailable_isolated(self):
        rows = [
            sample("legacy", "A", "QUOTE_MARKET_OBSERVED", price=1, liq=30000, vol=300, buys=4, sells=1, age=2),
            sample("proposed", "A", "QUOTE_MARKET_OBSERVED", price=1, liq=30000, vol=300, buys=4, sells=1, age=2),
            sample("legacy", "B", "LIQUIDITY_UNKNOWN", price=1, vol=300, buys=4, sells=1, age=2),
            sample("proposed", "C", "MARKET_DATA_UNAVAILABLE"),
        ]
        result = analyze(rows)
        self.assertEqual(result["groups"]["shared"]["observations"], 1)
        self.assertEqual(result["groups"]["shared"]["base_pass_with_age"], 1)
        self.assertEqual(result["groups"]["legacy_only"]["liquidity_unknown"], 1)
        self.assertEqual(result["groups"]["proposed_only"]["market_data_unavailable"], 1)
        self.assertEqual(result["coverage"]["shared_status_mismatches"], 0)

    def test_repeated_unavailable_remains_diagnostic_only(self):
        rows = []
        for minute in range(4):
            stamp = "2026-11-14T12:%02d:00+00:00" % minute
            rows.append(sample("proposed", "C", "MARKET_DATA_UNAVAILABLE", stamp=stamp))
        r = analyze(rows)
        self.assertEqual(r["proposed_only_repeatedly_unavailable"]["longest_streak"], 4)
        self.assertEqual(r["groups"]["proposed_only"]["unique_pairs"], 1)

    def test_sqlite_report_does_not_mutate_existing_data(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "comparison.sqlite"
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE selection_cohort_samples(
                    observed_at TEXT, cohort TEXT, mint TEXT, pair TEXT, status TEXT,
                    price_usd REAL, liquidity_usd REAL, volume_m5 REAL,
                    buys_m5 REAL, sells_m5 REAL, pair_age_hours REAL)""")
                conn.execute("INSERT INTO selection_cohort_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             sample("proposed", "C", "MARKET_DATA_UNAVAILABLE"))
            r = report(path, 24, datetime(2026, 11, 14, 13, tzinfo=timezone.utc))
            self.assertEqual(r["groups"]["proposed_only"]["observations"], 1)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM selection_cohort_samples").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
