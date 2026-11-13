import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from research_pipeline_quality_audit import audit, distribution, signal_audit, quote_audit

class QualityAuditTests(unittest.TestCase):
    def test_distribution(self):
        self.assertEqual(distribution([])["count"],0)
        self.assertEqual(distribution([10,20,30])["median"],20)

    def test_retained_momentum_repeats_are_not_unique_tokens(self):
        events=[
            {"mint":"A","pair":"p","momentum_research":{"status":"MOMENTUM_MATCH_RESEARCH_ONLY"},"reasons":[]},
            {"mint":"A","pair":"p","momentum_research":{"status":"MOMENTUM_MATCH_RESEARCH_ONLY"},"reasons":[]},
            {"mint":"B","pair":"p","momentum_research":{"status":"MOMENTUM_MATCH_RESEARCH_ONLY"},"reasons":[]},
        ]
        r=signal_audit({"events":events,"research_candidates":[{"mint":"A"}]})
        self.assertEqual(r["retained_momentum_matches"],3)
        self.assertEqual(r["unique_matching_pairs_in_retained_events"],2)
        self.assertEqual(r["repeat_momentum_observations"],1)

    def test_http_failures_are_not_hidden(self):
        q={"positions":{"x":{"entry_status":"QUOTE_AVAILABLE","exit_quotes":[
            {"status":"QUOTE_HTTP_ERROR","quoted_utc":"2026-11-13T17:00:00+00:00"},
            {"status":"QUOTE_AVAILABLE","quoted_utc":"2026-11-13T17:01:00+00:00"}]}}}
        r=quote_audit(q,{"candidates":{}})
        self.assertEqual(r["exit_status_counts"]["QUOTE_HTTP_ERROR"],1)
        self.assertEqual(r["measured_entry_http_latency_ms"]["count"],0)

    def test_read_only_sqlite_and_unknown_liquidity(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath=Path(folder)/"minutes.db"
            with sqlite3.connect(dbpath) as db:
                db.execute("""CREATE TABLE minute_observations(
                    observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
                    price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                    buys_m5 REAL,sells_m5 REAL)""")
                db.executemany("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?)",[
                    ("2026-11-13T17:00:00+00:00","A","pair","LIQUIDITY_UNKNOWN",1,None,300,4,1),
                    ("2026-11-13T17:00:00+00:00","B","pair","QUOTE_MARKET_OBSERVED",1,30000,300,4,1),
                ])
            now=datetime(2026,11,13,17,1,tzinfo=timezone.utc)
            r=audit(dbpath,{"events":[],"research_candidates":[]},
                    {"candidates":{}},{"positions":{}},now)
            self.assertEqual(r["market"]["latest_liquidity_unknown_pct"],50)
            self.assertEqual(r["market"]["liquidity_unknown_with_other_market_fields"],1)
            self.assertEqual(next(x for x in r["checks"] if x["name"]=="liquidity_data_coverage")["status"],"WARN")
            self.assertEqual(next(x for x in r["checks"] if x["name"]=="jupiter_new_http_timing")["status"],"WAITING_FOR_NEW_ENTRY")
            with sqlite3.connect(dbpath) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM minute_observations").fetchone()[0],2)

if __name__=="__main__":
    unittest.main()
