import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from research_minute_market_pilot import extract, record, initialize, source_candidates

class MinutePilotTests(unittest.TestCase):
    def test_exact_pair_and_missing_no_fake_zero(self):
        selected=[("mint","pair1","2026-11-12T14:00:00+00:00")]
        payload=[
          {"chainId":"solana","baseToken":{"address":"mint"},"pairAddress":"pair2","priceUsd":"9"},
          {"chainId":"solana","baseToken":{"address":"mint"},"pairAddress":"pair1",
           "priceUsd":"1.2","liquidity":{"usd":34000},"volume":{"m5":4000},
           "txns":{"m5":{"buys":11,"sells":4}}}
        ]
        observed=extract(payload,selected)
        self.assertEqual(observed["mint"]["price"],1.2)
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            record(db,"2026-11-12T14:01:00+00:00",selected,{})
            row=db.execute("SELECT status,liquidity_usd FROM minute_observations").fetchone()
            self.assertEqual(row,("MARKET_DATA_UNAVAILABLE",None))

    def test_source_pool_selection(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE snapshots (token_address TEXT, pair_address TEXT, scanned_at TEXT, volume_m5 REAL, liquidity_usd REAL, pair_age_hours REAL)")
            db.execute("INSERT INTO snapshots VALUES ('mint','pair','2026-11-12T14:00:00+00:00',3500,30000,1)")
            self.assertEqual(source_candidates(db,datetime(2026,11,12,14,1,tzinfo=timezone.utc))[0][0],"mint")

if __name__=="__main__":unittest.main()
