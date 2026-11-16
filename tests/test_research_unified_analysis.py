import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from research_unified_analysis import analyse,blockers,quality_report

class UnifiedTests(unittest.TestCase):
    def test_multiple_rejection_reasons_fail_closed(self):
        row=("t","mint","pair","DISCOVERY","PROFILE",1,None,100,1,5,None,
             "LIQUIDITY_UNKNOWN",0,0,"AWAITING_SECOND_SAMPLE")
        flags=blockers(row)
        self.assertIn("LIQUIDITY_UNKNOWN",flags)
        self.assertIn("VOLUME_BELOW_250",flags)
        self.assertIn("BUY_PRESSURE_WEAK",flags)
        self.assertIn("PAIR_AGE_UNKNOWN",flags)

    def test_full_one_command_report_reads_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            minute=root/"minute.db";v1=root/"v1.db";v2=root/"v2.db"
            log=root/"runs.jsonl"
            with sqlite3.connect(minute) as db:
                db.execute("CREATE TABLE minute_observations(observed_at TEXT,status TEXT)")
                db.executemany("INSERT INTO minute_observations VALUES(?,?)",[
                    ("t","LIQUIDITY_UNKNOWN"),("t","QUOTE_MARKET_OBSERVED")])
            with sqlite3.connect(v1) as db:
                db.execute("""CREATE TABLE discovery_cycles (
                   observed_at TEXT,baseline_selected INT,expanded_selected INT,
                   baseline_known INT,expanded_known INT,baseline_base INT,expanded_base INT,
                   baseline_momentum INT,expanded_momentum INT)""")
                db.execute("INSERT INTO discovery_cycles VALUES('t',30,100,8,15,1,1,0,0)")
                db.execute("CREATE TABLE discovery_signals(mint TEXT)")
            with sqlite3.connect(v2) as db:
                db.execute("""CREATE TABLE v2_samples (
                    observed_at TEXT,mint TEXT,pair TEXT,cohort TEXT,feed TEXT,
                    price_usd REAL,liquidity_usd REAL,volume_m5 REAL,
                    buys_m5 REAL,sells_m5 REAL,pair_age_hours REAL,
                    status TEXT,base_pass INT,momentum_pass INT,momentum_reason TEXT)""")
                db.executemany("INSERT INTO v2_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[
                    ("t","m","p","DISCOVERY","PROFILE",1,None,100,2,4,None,"LIQUIDITY_UNKNOWN",0,0,"AWAITING_SECOND_SAMPLE"),
                    ("t","n","p","BASELINE","LEARNING_DB",1,30000,300,8,1,1,"QUOTE_MARKET_OBSERVED",1,0,"AWAITING_SECOND_SAMPLE")])
                db.execute("CREATE TABLE v2_cycles(observed_at TEXT,newly_unique_discovery_signals INT)")
                db.execute("INSERT INTO v2_cycles VALUES('t',0)")
                db.execute("CREATE TABLE v2_signals(cohort TEXT)")
            log.write_text(json.dumps({"status":"SUCCESS","mode":"PHASE3_ISOLATED_EVENT_DRIVEN_PILOT",
                   "b3_to_m1_start_ms":0.1,"b3_new_candidates":0,"m1_new_candidates":0})+"\n")
            before=[p.read_bytes() for p in (minute,v1,v2,log)]
            r=analyse(minute,v1,v2,log)
            self.assertEqual(r["minute_market"]["unknown_pct"],50)
            self.assertEqual(r["discovery_v1"]["cycles"],1)
            self.assertEqual(r["discovery_v2"]["recent_sample_quality"]["DISCOVERY"]["base_blockers_overlapping"]["LIQUIDITY_UNKNOWN"],1)
            self.assertEqual(r["phase3_handoff"]["successful_cycles"],1)
            self.assertEqual(before,[p.read_bytes() for p in (minute,v1,v2,log)])
            self.assertEqual(r["safety"]["http_requests"],0)

    def test_missing_inputs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            r=analyse(*(root/x for x in ("missing1","missing2","missing3","missing4")))
            self.assertEqual(r["recommendation"]["priority"],"CHECK_V2_DATA")

    def test_sample_limit_validation(self):
        with self.assertRaises(ValueError):
            analyse(limit=0)

if __name__=="__main__":
    unittest.main()
