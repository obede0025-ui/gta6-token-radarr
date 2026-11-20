import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from research_weighted_momentum_shadow import analyze,decisions,score

START=datetime(2026,11,20,12,0,tzinfo=timezone.utc)

class WeightedShadowTests(unittest.TestCase):
    def test_warning_is_separate_and_score_freezes(self):
        feat={"price_accel_pp":10,"liquidity_accel_pp":10,
              "buy_share_accel_pp":2,"liquidity_at_signal":30000,
              "liquidity_at_decision":100}
        d=decisions(feat)
        self.assertTrue(d["strict_all_positive"])
        self.assertTrue(d["balanced_fixed"])
        self.assertTrue(d["liquidity_risk_at_3m"])

    def test_first_eligible_mint_across_multiple_pairs_and_no_lookahead(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        db=Path(t.name)/"pilot.sqlite"
        with sqlite3.connect(db) as con:
            con.execute("""CREATE TABLE minute_observations(
                observed_at TEXT,mint TEXT,pair TEXT,status TEXT,price_usd REAL,
                liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
                pair_age_hours REAL)""")
            # Early pair Z, later pair A: first eligible must be chronological.
            rows=[
                (0,"Z",1.0,30000,1000,7,3),
                (60,"Z",1.05,31500,1100,8,3),
                (180,"Z",1.2,34000,1300,10,3),
                (300,"Z",1.4,35000,1500,10,4),
                (30,"A",1,30000,1000,7,3),
                (330,"A",9,30000,1000,7,3)]
            for s,pair,price,liq,vol,buys,sells in rows:
                stamp=(START+timedelta(seconds=s)).isoformat()
                con.execute("INSERT INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (stamp,"TOKEN",pair,"QUOTE_MARKET_OBSERVED",
                      price,liq,vol,buys,sells,2))
        before=db.read_bytes()
        r=analyze(db,hours=1,as_of=START+timedelta(minutes=10))
        self.assertEqual(r["unique_first_eligible_mints"],1)
        self.assertEqual(r["evaluated_mints"],1)
        self.assertEqual(r["records"][0]["pair"],"Z")
        self.assertEqual(r["records"][0]["outcome_label"],"MOMENTUM_WITH_LIQUIDITY")
        self.assertEqual(r["records"][0]["quote_status"],"NOT_AVAILABLE_IN_MINUTE_DB")
        self.assertEqual(before,db.read_bytes())

if __name__=="__main__":unittest.main()
