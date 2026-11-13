import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from research_minute_market_pilot import extract, record, initialize, source_candidates, adaptive_candidate_preview

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

    def test_liquidity_unknown_distinct_from_zero(self):
        selected=[("mint","pair","2026-11-12T14:00:00+00:00")]
        payload=[{"chainId":"solana","baseToken":{"address":"mint"},
                  "pairAddress":"pair","priceUsd":"1.25",
                  "volume":{"m5":1200},"txns":{"m5":{"buys":10,"sells":2}}}]
        found=extract(payload,selected)
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            record(db,"2026-11-12T14:01:00+00:00",selected,found)
            status,price,liq=db.execute(
                "SELECT status,price_usd,liquidity_usd FROM minute_observations"
            ).fetchone()
            self.assertEqual((status,price,liq),("LIQUIDITY_UNKNOWN",1.25,None))
        found["mint"]["liquidity"]=0.0
        with sqlite3.connect(":memory:") as db:
            initialize(db)
            record(db,"2026-11-12T14:02:00+00:00",selected,found)
            self.assertEqual(db.execute(
                "SELECT status FROM minute_observations").fetchone()[0],
                "QUOTE_MARKET_OBSERVED")

    def test_source_pool_selection(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE snapshots (token_address TEXT, pair_address TEXT, scanned_at TEXT, volume_m5 REAL, liquidity_usd REAL, pair_age_hours REAL)")
            db.execute("INSERT INTO snapshots VALUES ('mint','pair','2026-11-12T14:00:00+00:00',3500,30000,1)")
            self.assertEqual(source_candidates(db,datetime(2026,11,12,14,1,tzinfo=timezone.utc))[0][0],"mint")

    def test_monitor_keeps_low_liquidity_and_volume(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE snapshots (token_address TEXT, pair_address TEXT, scanned_at TEXT, volume_m5 REAL, liquidity_usd REAL, pair_age_hours REAL)")
            db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", [
                ("mintA","pairA","2026-11-12T13:50:00+00:00",4000,35000,1),
                ("mintA","pairA","2026-11-12T14:00:00+00:00",4,100,1.2),
                ("mintB","pairB","2026-11-12T14:00:00+00:00",6,500,3),
            ])
            picked=source_candidates(db,datetime(2026,11,12,14,1,tzinfo=timezone.utc))
            self.assertEqual([x[0] for x in picked], ["mintA", "mintB"])
            self.assertEqual(picked[0][1], "pairA")

    def test_shadow_preview_preserves_baseline_and_rotates_stagnant_unknown(self):
        now=datetime(2026,11,13,16,40,tzinfo=timezone.utc)
        with sqlite3.connect(":memory:") as source, sqlite3.connect(":memory:") as pilot:
            source.execute("CREATE TABLE snapshots (token_address TEXT,pair_address TEXT,scanned_at TEXT,volume_m5 REAL,liquidity_usd REAL,pair_age_hours REAL)")
            source.executemany("INSERT INTO snapshots VALUES(?,?,?,?,?,?)",[
                ("a","pa","2026-11-13T16:35:00+00:00",500,30000,1),
                ("b","pb","2026-11-13T16:35:00+00:00",450,30000,1),
                ("c","pc","2026-11-13T16:35:00+00:00",400,30000,1),
                ("d","pd","2026-11-13T16:35:00+00:00",350,30000,1),
                ("e","pe","2026-11-13T16:35:00+00:00",300,30000,1)
            ])
            initialize(pilot)
            for i in range(12):
                pilot.execute("""INSERT INTO minute_observations
                  (observed_at,mint,pair,source_snapshot_at,status,price_usd,liquidity_usd,volume_m5)
                  VALUES(?,?,?,?,?,?,?,?)""",(
                    f"2026-11-13T16:{20+i:02d}:00+00:00","c","pc",
                    "2026-11-13T16:15:00+00:00","LIQUIDITY_UNKNOWN",1,None,0))
            result=adaptive_candidate_preview(source,pilot,now,limit=4)
            self.assertEqual([x[0] for x in result["legacy"]],["a","b","c","d"])
            self.assertEqual([x[0] for x in result["proposed"]],["a","b","d","e"])
            self.assertEqual(result["diagnostics"]["changed_slots"],1)
            self.assertTrue(result["diagnostics"]["shadow_only"])

    def test_shadow_preview_keeps_unknown_if_recent_volume_active(self):
        now=datetime(2026,11,13,16,40,tzinfo=timezone.utc)
        with sqlite3.connect(":memory:") as source, sqlite3.connect(":memory:") as pilot:
            source.execute("CREATE TABLE snapshots (token_address TEXT,pair_address TEXT,scanned_at TEXT,volume_m5 REAL,liquidity_usd REAL,pair_age_hours REAL)")
            source.executemany("INSERT INTO snapshots VALUES(?,?,?,?,?,?)",[
                (x,"p"+x,"2026-11-13T16:35:00+00:00",v,30000,1)
                for x,v in [("a",500),("b",450),("c",400),("d",350),("e",300)]])
            initialize(pilot)
            for i in range(12):
                pilot.execute("""INSERT INTO minute_observations
                  (observed_at,mint,pair,source_snapshot_at,status,price_usd,liquidity_usd,volume_m5)
                  VALUES(?,?,?,?,?,?,?,?)""",(
                    f"2026-11-13T16:{20+i:02d}:00+00:00","c","pc",
                    "2026-11-13T16:15:00+00:00","LIQUIDITY_UNKNOWN",1,None,300))
            result=adaptive_candidate_preview(source,pilot,now,limit=4)
            self.assertEqual([x[0] for x in result["proposed"]],["a","b","c","d"])
            self.assertEqual(result["diagnostics"]["changed_slots"],0)

if __name__=="__main__":unittest.main()
