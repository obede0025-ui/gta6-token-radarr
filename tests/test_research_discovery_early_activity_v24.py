import sqlite3
import tempfile
import unittest
from pathlib import Path

from research_discovery_early_activity_v24 import compare,analyse

T0="2026-11-16T18:28:00+00:00"
T1="2026-11-16T18:29:00+00:00"
T2="2026-11-16T18:30:00+00:00"

def point(stamp,vol,buys,sells,base=True,momentum=False):
    return {"timestamp":stamp,"volume":vol,"buys":buys,"sells":sells,
            "base_pass":base,"momentum_pass":momentum}

class EarlyActivityTests(unittest.TestCase):
    def test_watch_requires_current_base_and_valid_gap(self):
        first=point(T0,1000,80,10)
        second=point(T1,990,100,10)
        check=compare(second,first)
        self.assertTrue(check["watch_only"])
        self.assertFalse(check["three_sample_persistence"])
        self.assertFalse(compare(point(T1,990,100,10,False),first)["watch_only"])
        self.assertFalse(compare(second,None)["watch_only"])

    def test_persistence_only_three_spaced_selected_samples(self):
        a=point(T0,1000,80,10)
        b=point(T1,990,100,10)
        c=point(T2,970,125,10)
        self.assertTrue(compare(c,b,a)["three_sample_persistence"])
        self.assertFalse(compare(c,b,point(T1,1000,80,10))["three_sample_persistence"])

    def test_declining_volume_rejected_even_with_buy_dominance(self):
        old=point(T0,20000,200,20)
        new=point(T1,14000,300,20)
        result=compare(new,old)
        self.assertFalse(result["watch_only"])
        self.assertIn("ROLLING_VOLUME_FALLING_FAST",result["reasons"])

    def test_read_only_cohort_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath=Path(tmp)/"v22.db"
            with sqlite3.connect(dbpath) as db:
                db.execute("""CREATE TABLE quality_cohort_samples (
                   observed_at TEXT,mint TEXT,pair TEXT,cohort TEXT,
                   price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                   buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL,
                   base_pass INTEGER,momentum_pass INTEGER)""")
                for stamp,vol,buys in [(T0,1000,80),(T1,990,100),(T2,970,125)]:
                    db.execute("INSERT INTO quality_cohort_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (stamp,"mint","pair","ACTIVITY_FIRST",1,30000,vol,buys,10,1,1,0))
                db.execute("INSERT INTO quality_cohort_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (T2,"mint2","pair2","FEED_ORDER",1,None,100,1,10,1,0,0))
            original=dbpath.read_bytes()
            report=analyse(dbpath)
            self.assertEqual(report["cohorts"]["ACTIVITY_FIRST"]["persistent_3_sample_watch_pairs"],1)
            self.assertEqual(report["cohorts"]["FEED_ORDER"]["early_watch_only_pairs"],0)
            self.assertEqual(original,dbpath.read_bytes())

    def test_missing_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(analyse(Path(tmp)/"absent.db")["status"],"NO_DATABASE")
        with self.assertRaises(ValueError):
            analyse(max_rows=0)

if __name__=="__main__":
    unittest.main()
