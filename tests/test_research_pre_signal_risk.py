import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_pre_signal_risk import previous,report
from research_jupiter_minute_quotes import dt

class PreSignalRiskTests(unittest.TestCase):
    def test_never_uses_future_or_wrong_pair_and_respects_lookback(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("""CREATE TABLE minute_observations (
               observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
               liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
               pair_age_hours REAL,price_usd REAL)""")
            db.executemany("""INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?,?)""",[
               ("2026-11-14T17:55:00+00:00","a","pairA","QUOTE_MARKET_OBSERVED",30000,300,4,1,1,0.1),
               ("2026-11-14T18:01:00+00:00","a","pairA","QUOTE_MARKET_OBSERVED",99000,900,9,1,1,0.2),
               ("2026-11-14T17:59:00+00:00","a","pairB","QUOTE_MARKET_OBSERVED",50000,500,5,1,1,0.3)])
            stamp=dt("2026-11-14T18:00:00+00:00")
            row=previous(db,"minute_observations","a","pairA",stamp,["liquidity_usd"])
            self.assertEqual(row["liquidity_usd"],30000)
            self.assertEqual(row["lag_seconds"],300.0)
            self.assertIsNone(previous(db,"minute_observations","a","pairMissing",stamp,["liquidity_usd"]))
            self.assertIsNone(previous(db,"minute_observations","a","pairA",dt("2026-11-14T18:20:00+00:00"),["liquidity_usd"]))

    def test_report_preserves_missing_instead_of_filling_future(self):
        with tempfile.TemporaryDirectory() as folder:
            m=Path(folder)/"minute.sqlite";o=Path(folder)/"original.sqlite"
            with sqlite3.connect(m) as db:
                db.execute("""CREATE TABLE minute_observations(
                   observed_at TEXT,mint TEXT,pair TEXT,status TEXT,liquidity_usd REAL,
                   volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL,price_usd REAL)""")
                db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                           ("2026-11-14T18:01:00+00:00","a","pairA","QUOTE_MARKET_OBSERVED",30000,300,5,1,1,0.2))
            with sqlite3.connect(o) as db:
                db.execute("""CREATE TABLE snapshots(
                  token_address TEXT,pair_address TEXT,scanned_at TEXT,
                  liquidity_usd REAL,volume_m5 REAL,pair_age_hours REAL)""")
            state={"mode":"JUPITER_MINUTE_QUOTE_ONLY","positions":{
                "a":{"mint":"a","pair":"pairA",
                     "signal_utc":"2026-11-14T18:00:00+00:00",
                     "entry_quote_utc":"2026-11-14T18:00:10+00:00",
                     "entry_token_raw":100,"exit_quotes":[]}}}
            result=report(state,m,o)
            self.assertEqual(result["coverage"]["missing"],1)
            self.assertIsNone(result["other_unmatched"][0].get("liquidity_usd"))

if __name__=="__main__":unittest.main()
