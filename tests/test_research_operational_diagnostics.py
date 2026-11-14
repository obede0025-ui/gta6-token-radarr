import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from research_operational_diagnostics import (
    jupiter_diagnosis, report, selection_diagnosis,
)


class OperationalDiagnosticsTests(unittest.TestCase):
    def test_separates_old_from_recent_jupiter_errors(self):
        now = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
        state = {"positions": {"one": {"exit_quotes": [
            {"status": "QUOTE_HTTP_ERROR", "http_status": 429,
             "quoted_utc": "2026-11-12T11:00:00+00:00"},
            {"status": "QUOTE_HTTP_ERROR", "http_status": 503,
             "quoted_utc": "2026-11-14T16:30:00+00:00"},
            {"status": "QUOTE_AVAILABLE", "quoted_utc": "2026-11-14T16:45:00+00:00"}
        ]}}}
        r = jupiter_diagnosis(state, now, 24)
        self.assertEqual(r["historical_statuses"]["QUOTE_HTTP_ERROR"], 2)
        self.assertEqual(r["recent_failure_count"], 1)
        self.assertEqual(r["recent_http_error_codes"]["503"], 1)
        self.assertEqual(r["recent_failure_pct"], 50.0)

    def test_read_only_selection_with_missing_exact_pair_rates(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / "ab.sqlite"
            statepath = Path(folder) / "quotes.json"
            with sqlite3.connect(dbpath) as db:
                db.execute("""CREATE TABLE selection_cohort_samples(
                    observed_at TEXT, cohort TEXT, mint TEXT, pair TEXT,
                    status TEXT, price_usd REAL, liquidity_usd REAL,
                    volume_m5 REAL, buys_m5 REAL, sells_m5 REAL, pair_age_hours REAL)""")
                db.executemany(
                    "INSERT INTO selection_cohort_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        ("2026-11-14T16:00:00+00:00", "legacy", "A", "p", "QUOTE_MARKET_OBSERVED", 1, 30000, 300, 4, 1, 2),
                        ("2026-11-14T16:00:00+00:00", "proposed", "B", "q", "MARKET_DATA_UNAVAILABLE", None, None, None, None, None, None),
                    ],
                )
                db.execute("""CREATE TABLE comparison_cycles(
                    observed_at TEXT, legacy_missing INTEGER, proposed_missing INTEGER,
                    error TEXT)""")
                db.execute("INSERT INTO comparison_cycles VALUES(?,?,?,?)",
                           ("2026-11-14T16:00:00+00:00", 0, 1, None))
            statepath.write_text(json.dumps({"positions": {}}))
            now = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
            result = report(dbpath, statepath, 24, now)
            self.assertEqual(result["selection"]["proposed"]["missing_exact_pair_cycle_count"], 1)
            self.assertEqual(result["selection"]["proposed"]["market_data_unavailable"], 1)
            self.assertIn("PROPOSED_HAS_MORE_MISSING_EXACT_PAIRS", result["findings"])
            with sqlite3.connect(dbpath) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM selection_cohort_samples").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
