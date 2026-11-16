import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research_minute_handoff_phase2 import audit, simulate, verify_sources
from research_m1_minute_followup import initialize

NOW=datetime(2026,11,16,14,0,tzinfo=timezone.utc)

def b3_state():
    return {"version":1,"mode":"MINUTE_B3_SAFETY_SHADOW",
            "cursor":["2026-11-16T13:59:00+00:00","M","P"],
            "events":[],"research_candidates":[],"research_seen_pairs":[],
            "total_logged":0,"last_checked_utc":NOW.isoformat()}

def empty_db(conn):
    conn.execute("""CREATE TABLE minute_observations
      (observed_at TEXT,mint TEXT,pair TEXT,source_snapshot_at TEXT,status TEXT,
       price_usd REAL,liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,
       sells_m5 REAL,pair_age_hours REAL)""")

class PhaseTwoTests(unittest.TestCase):
    def test_duplicate_calls_do_not_admit_again(self):
        b3=b3_state()
        m1=initialize(NOW-timedelta(minutes=3),b3)
        source=copy.deepcopy(b3)
        source["research_candidates"]=[{
            "mint":"M","pair":"P","signal_utc":(NOW-timedelta(seconds=30)).isoformat(),
            "observed_price_usd":1.0,"observed_liquidity_usd":30000,
            "observed_volume_m5":1000,"research_only":True}]
        with sqlite3.connect(":memory:") as db:
            empty_db(db)
            def fake_advance(b,unused_db,now):
                b["research_candidates"]=copy.deepcopy(source["research_candidates"])
                return {"added":0,"new_research_candidates":1}
            original_b=copy.deepcopy(b3)
            original_m=copy.deepcopy(m1)
            with patch("research_minute_handoff_phase2.b3_module.advance",side_effect=fake_advance):
                r=simulate(b3,m1,db,NOW)
            self.assertEqual(r["m1_unique_new_candidates_if_immediate"],1)
            self.assertEqual(r["m1_duplicated_run_new_candidates"],0)
            self.assertEqual(original_b,b3)
            self.assertEqual(original_m,m1)

    def test_reject_incompatible_state(self):
        b3=b3_state()
        m1=initialize(NOW,b3)
        m1["rules"]=dict(m1["rules"])
        m1["rules"]["tp_pct"]=99
        with self.assertRaises(ValueError):
            verify_sources(b3,m1)

    def test_audit_does_not_modify_input_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            b3=b3_state()
            m1=initialize(NOW-timedelta(minutes=1),b3)
            bp=root/"b3.json"
            mp=root/"m1.json"
            dp=root/"data.sqlite"
            bp.write_text(json.dumps(b3))
            mp.write_text(json.dumps(m1))
            with sqlite3.connect(dp) as db:
                empty_db(db)
            prior=(bp.read_bytes(),mp.read_bytes(),dp.read_bytes())
            report=audit(bp,mp,dp,NOW)
            self.assertEqual(report["m1_unique_new_candidates_if_immediate"],0)
            self.assertEqual(prior,(bp.read_bytes(),mp.read_bytes(),dp.read_bytes()))

if __name__=="__main__":
    unittest.main()
