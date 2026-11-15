import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_forward_age_shadow import (
    initialize_epoch, load_epoch, classify, compare, report, prior_age
)
from research_jupiter_minute_quotes import dt

class ForwardAgeShadowTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.epoch=root/"epoch.json"
        self.minute=root/"minute.sqlite"
        self.original=root/"original.sqlite"
        with sqlite3.connect(self.minute) as db:
            db.execute("""CREATE TABLE minute_observations(
                observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
                liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
                pair_age_hours REAL,price_usd REAL)""")
            db.executemany("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?,?)",[
                ("2026-11-15T10:01:00+00:00","a","pa","QUOTE_MARKET_OBSERVED",
                 30000,800,6,2,.6,.01),
                ("2026-11-15T10:02:00+00:00","a","pa","QUOTE_MARKET_OBSERVED",
                 30000,800,6,2,.1,.01),
                ("2026-11-15T10:01:00+00:00","a","other","QUOTE_MARKET_OBSERVED",
                 30000,800,6,2,.01,.01)])
        with sqlite3.connect(self.original) as db:
            db.execute("""CREATE TABLE snapshots(
                token_address TEXT,pair_address TEXT,scanned_at TEXT,
                liquidity_usd REAL,volume_m5 REAL,pair_age_hours REAL)""")
        initialize_epoch(self.epoch,dt("2026-11-15T10:00:00+00:00"))

    def make_pos(self,signal="2026-11-15T10:02:00+00:00"):
        return {"mint":"a","pair":"pa","signal_utc":signal,
                "entry_quote_utc":"2026-11-15T10:02:05+00:00",
                "entry_token_raw":100,"exit_quotes":[
                   {"status":"QUOTE_AVAILABLE",
                    "quoted_utc":"2026-11-15T10:02:05+00:00",
                    "estimated_roundtrip_pct":-1.0},
                   {"status":"QUOTE_AVAILABLE",
                    "quoted_utc":"2026-11-15T10:05:05+00:00",
                    "estimated_roundtrip_pct":12.0}]}

    def test_frozen_start_cannot_be_reset(self):
        with self.assertRaises(FileExistsError):
            initialize_epoch(self.epoch,dt("2026-11-15T11:00:00+00:00"))
        self.assertEqual(load_epoch(self.epoch)["started_utc"],
                         "2026-11-15T10:00:00.000000+00:00")

    def test_past_only_age_and_matured_results(self):
        with sqlite3.connect(self.minute) as m,sqlite3.connect(self.original) as o:
            r=classify(self.make_pos(),dt("2026-11-15T10:00:00+00:00"),
                       dt("2026-11-15T11:00:00+00:00"),m,o)
        self.assertAlmostEqual(r["pre_signal_age_minutes"],36)
        self.assertEqual(r["pre_signal_sample_lag_seconds"],60)
        self.assertEqual(r["outcome"],"TP_QUOTE_OBSERVED")
        self.assertEqual(compare([r])["B_3pct_plus_30min"]["outcomes"]["TP_QUOTE_OBSERVED"],1)

    def test_pending_not_reported_as_historical_tp(self):
        with sqlite3.connect(self.minute) as m,sqlite3.connect(self.original) as o:
            r=classify(self.make_pos(),dt("2026-11-15T10:00:00+00:00"),
                       dt("2026-11-15T10:04:00+00:00"),m,o)
        self.assertEqual(r["outcome"],"PENDING_HORIZON")
        self.assertIsNone(r["stress_quote_pct"])
        self.assertEqual(compare([r])["B_3pct_plus_30min"]["observable_exits"],0)

    def test_old_signal_excluded_even_if_later_entry(self):
        p=self.make_pos("2026-11-15T09:59:00+00:00")
        with sqlite3.connect(self.minute) as m,sqlite3.connect(self.original) as o:
            self.assertIsNone(classify(p,dt("2026-11-15T10:00:00+00:00"),
                                       dt("2026-11-15T11:00:00+00:00"),m,o))

    def test_report_read_only_and_missing_age_distinct(self):
        state=Path(self.folder.name)/"jupiter.json"
        pos=self.make_pos()
        pos["pair"]="missing"
        state.write_text(json.dumps({"mode":"JUPITER_MINUTE_QUOTE_ONLY",
                                      "positions":{"a":pos}}))
        result=report(self.epoch,state,self.minute,self.original,
                      now=dt("2026-11-15T11:00:00+00:00"))
        self.assertEqual(result["A_3pct_gate"]["candidates"],1)
        self.assertEqual(result["B_3pct_plus_30min"]["candidates"],0)
        self.assertEqual(result["B_unknown_age_not_admitted"]["candidates"],1)
        self.assertEqual(result["verdict"],"INSUFFICIENT_FORWARD_SAMPLE")

if __name__=="__main__":unittest.main()
