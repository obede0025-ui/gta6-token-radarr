import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from research_liquidity_spike_classifier import analyze, classify

START=datetime(2026,11,20,18,0,tzinfo=timezone.utc)

class SpikeClassifierTest(unittest.TestCase):
    def test_crash_precedes_any_verified_quote_label(self):
        label, quote=classify(.00004247,.4256,32684.32,401.37)
        self.assertEqual(label,"MOMENTUM_LIQUIDITY_COLLAPSE")
        self.assertEqual(quote,"SELL_QUOTE_UNAVAILABLE_OR_UNCHECKED")
        label2,quote2=classify(.00004247,.4256,32684.32,401.37,True)
        self.assertEqual(label2,"MOMENTUM_LIQUIDITY_COLLAPSE")
        self.assertEqual(quote2,"SELL_QUOTE_VERIFIED_RESEARCH")

    def test_liquidity_vanishes_with_price_crash(self):
        label,quote=classify(2.4e-5,5.5e-9,24065.94,0)
        self.assertEqual(label,"LIQUIDITY_COLLAPSE_WITH_PRICE_LOSS")
        self.assertEqual(quote,"SELL_QUOTE_UNAVAILABLE_OR_UNCHECKED")

    def test_spike_and_normal_momentum(self):
        self.assertEqual(classify(1,3,30000,31000)[0],"PRICE_SPIKE_UNVERIFIED")
        self.assertEqual(classify(1,1.3,30000,31000)[0],"MOMENTUM_WITH_LIQUIDITY")
        self.assertEqual(classify(1,1.1,30000,31000)[0],"NO_QUALIFIED_MOMENTUM")
        self.assertEqual(classify(1,None,30000,31000)[0],"INSUFFICIENT_MARKET_EVIDENCE")

    def test_sql_read_only_real_observation_horizon_and_pair_isolation(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        db=Path(t.name)/"minute.sqlite"
        with sqlite3.connect(db) as con:
            con.execute("""CREATE TABLE minute_observations (
                observed_at TEXT,mint TEXT,pair TEXT,status TEXT,
                price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL)""")
            for minutes,mint,pair,price,liq in [
                (0,"M","P1",.00004,30000),
                (5,"M","P1",.4,350),
                (5,"M","OTHER",.4,999999),
                (0,"Q","P2",1,30000),
                (7,"Q","P2",1.3,31000)]:
                stamp=(START+timedelta(minutes=minutes)).isoformat()
                con.execute("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (stamp,mint,pair,"QUOTE_MARKET_OBSERVED",price,liq,1000,7,3,1))
        before=db.read_bytes()
        r=analyze(db,hours=1,as_of=START+timedelta(minutes=10))
        self.assertEqual(r["evaluated_windows"],1)
        self.assertEqual(r["classifications"]["MOMENTUM_LIQUIDITY_COLLAPSE"],1)
        self.assertEqual(r["classifications"]["MISSING_5M_MEASUREMENT"],3)
        self.assertEqual(r["source_db_writes"],0)
        self.assertEqual(db.read_bytes(),before)

if __name__=="__main__":
    unittest.main()
