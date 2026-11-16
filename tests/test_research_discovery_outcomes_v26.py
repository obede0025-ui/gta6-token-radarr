import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from research_discovery_outcomes_v26 import (
    init,track_new,pending,store_observations,report,run
)
from research_discovery_shadow_v2 import API

START=datetime(2026,11,16,18,0,tzinfo=timezone.utc)
MINT="A"*44
PAIR="B"*44

class Resp:
    def __init__(self,rows):self.rows=rows
    def raise_for_status(self):pass
    def json(self):return self.rows

class Session:
    def __init__(self,price="1.1",liquidity=35000):self.price=price;self.liquidity=liquidity;self.calls=0
    def get(self,url,timeout=20):
        self.calls+=1
        self.assertion=url.startswith(API)
        return Resp([{"chainId":"solana","baseToken":{"address":MINT},
                      "pairAddress":PAIR,"priceUsd":self.price,
                      "liquidity":{"usd":self.liquidity}}])
    def close(self):pass

class OutcomeTests(unittest.TestCase):
    def make_dbs(self,folder):
        src=folder/"source.sqlite"
        dst=folder/"outcomes.sqlite"
        with sqlite3.connect(src) as db:
            db.execute("""CREATE TABLE quality_cohort_samples(
              observed_at TEXT,mint TEXT,pair TEXT,cohort TEXT,
              price_usd REAL,liquidity_usd REAL,momentum_pass INT)""")
            db.execute("INSERT INTO quality_cohort_samples VALUES (?,?,?,?,?,?,?)",
                       (START.isoformat(timespec="seconds"),MINT,PAIR,"FEED_ORDER",1.0,30000,1))
            db.execute("INSERT INTO quality_cohort_samples VALUES (?,?,?,?,?,?,?)",
                       (START.isoformat(timespec="seconds"),MINT,PAIR,"ACTIVITY_FIRST",1.0,30000,1))
        with sqlite3.connect(dst) as db:init(db)
        return src,dst

    def test_exact_pair_both_cohorts_and_cost_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=self.make_dbs(Path(tmp))
            with sqlite3.connect(dst) as db:
                self.assertEqual(track_new(src,db,START.isoformat(timespec="seconds")),2)
                self.assertEqual(track_new(src,db,START.isoformat(timespec="seconds")),0)
                self.assertEqual(len(pending(db,START+timedelta(seconds=59))[0]),0)
                session=Session()
                outcome=store_observations(db,START+timedelta(seconds=60),session)
                self.assertEqual(outcome["due"],2)
                self.assertEqual(session.calls,1)
                self.assertEqual(store_observations(db,START+timedelta(seconds=61),session)["due"],0)
            r=report(src,dst)
            self.assertEqual(r["unique_exact_pairs"],1)
            self.assertEqual(r["cohort_signal_records"],2)
            for row in r["signals"]:
                self.assertAlmostEqual(row["outcomes"]["60"]["gross_price_change_pct"],10.)
                self.assertAlmostEqual(row["outcomes"]["60"]["net_hypothetical_pct_after_cost"],8.)
                self.assertFalse(row["outcomes"]["60"]["executable_exit_verified"])

    def test_missing_pair_not_a_zero_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=self.make_dbs(Path(tmp))
            with sqlite3.connect(dst) as db:
                track_new(src,db,START.isoformat(timespec="seconds"))
                session=Session(price=None,liquidity=None)
                store_observations(db,START+timedelta(seconds=60),session)
            r=report(src,dst)
            self.assertEqual(r["signals"][0]["outcomes"]["60"]["status"],"PRICE_UNAVAILABLE")
            self.assertIsNone(r["signals"][0]["outcomes"]["60"]["net_hypothetical_pct_after_cost"])

    def test_late_horizon_not_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=self.make_dbs(Path(tmp))
            with sqlite3.connect(dst) as db:
                track_new(src,db,START.isoformat(timespec="seconds"))
                session=Session()
                out=store_observations(db,START+timedelta(seconds=1000),session)
                self.assertEqual(out["missed"],6)
                self.assertEqual(session.calls,0)
            r=report(src,dst)
            self.assertEqual(r["signals"][0]["outcomes"]["900"]["status"],"HORIZON_MISSED")

    def test_no_old_db_overwrite_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=self.make_dbs(Path(tmp))
            with self.assertRaises(FileExistsError):run(src,dst)
        with self.assertRaises(ValueError):run(cycles=19)
        with self.assertRaises(ValueError):run(cycles=25,discovery_cycles=11)

if __name__=="__main__":unittest.main()
