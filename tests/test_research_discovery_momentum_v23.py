import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_discovery_momentum_v23 import analyse,diagnose

STAMP1="2026-11-16T18:29:00+00:00"
STAMP2="2026-11-16T18:30:00+00:00"

def sample(stamp,mint,cohort,volume,base,passed,reason,buys=12,sells=2):
    return (stamp,mint,"pool",cohort,"PROFILE",1.0,30000.0,volume,
            buys,sells,1.0,"QUOTE_MARKET_OBSERVED",base,passed,reason)

class DiagnosisTests(unittest.TestCase):
    def test_volume_growth_and_buys(self):
        current=sample(STAMP2,"mint","FEED_ORDER",280,1,0,"NO_VOLUME_GROWTH")
        old=(STAMP1,300,12,2)
        item=diagnose(current,old)
        self.assertEqual(item["gap_seconds"],60)
        self.assertLess(item["volume_growth_pct"],0)
        self.assertIn("INSUFFICIENT_VOLUME_GROWTH",item["momentum_blockers"])

    def test_missing_prior_and_unknown_liquidity(self):
        current=sample(STAMP1,"mint","ACTIVITY_FIRST",300,1,0,"AWAITING_SECOND_SAMPLE")
        self.assertEqual(diagnose(current,None)["momentum_blockers"],["NO_PRIOR_SELECTED_PAIR_SAMPLE"])
        missing=list(current)
        missing[6]=None
        missing[11]="LIQUIDITY_UNKNOWN"
        missing[12]=0
        self.assertIn("LIQUIDITY_UNKNOWN",diagnose(tuple(missing),None)["base_blockers"])

    def test_full_read_only_report_and_cohort_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"quality.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE quality_cycles(observed_at TEXT PRIMARY KEY)")
                db.executemany("INSERT INTO quality_cycles VALUES(?)",[(STAMP1,),(STAMP2,)])
                db.execute("""CREATE TABLE quality_cohort_samples(
                  observed_at TEXT,mint TEXT,pair TEXT,cohort TEXT,feed TEXT,
                  price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                  buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL,
                  status TEXT,base_pass INT,momentum_pass INT,momentum_reason TEXT)""")
                db.executemany("INSERT INTO quality_cohort_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[
                    sample(STAMP1,"one","FEED_ORDER",300,1,0,"AWAITING_SECOND_SAMPLE"),
                    sample(STAMP2,"one","FEED_ORDER",280,1,0,"NO_VOLUME_GROWTH"),
                    sample(STAMP2,"one","ACTIVITY_FIRST",330,1,0,"AWAITING_SECOND_SAMPLE")])
            original=path.read_bytes()
            r=analyse(path,latest_cycles=2)
            self.assertEqual(r["cohorts"]["FEED_ORDER"]["base_pass_observations"],2)
            self.assertEqual(r["cohorts"]["ACTIVITY_FIRST"]["momentum_blockers_for_base_pass_overlapping"]["NO_PRIOR_SELECTED_PAIR_SAMPLE"],1)
            self.assertEqual(original,path.read_bytes())

    def test_missing_database_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(analyse(Path(tmp)/"missing")["status"],"NO_DATABASE")
        with self.assertRaises(ValueError):
            analyse(latest_cycles=0)

if __name__=="__main__":
    unittest.main()
