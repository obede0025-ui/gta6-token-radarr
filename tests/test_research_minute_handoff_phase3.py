import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research_minute_handoff_phase3 import run_once, report, paths_for
from research_m1_minute_followup import candidate_id

BASE=datetime(2026,11,16,14,0,tzinfo=timezone.utc)

def database(file):
    with sqlite3.connect(file) as db:
        db.execute("""CREATE TABLE minute_observations (
          observed_at TEXT, mint TEXT, pair TEXT, source_snapshot_at TEXT,
          status TEXT, price_usd REAL, liquidity_usd REAL, volume_m5 REAL,
          buys_m5 REAL, sells_m5 REAL, pair_age_hours REAL)""")

class Phase3Tests(unittest.TestCase):
    def test_real_isolated_bootstrap_and_no_duplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            dbfile=root/"minute.sqlite"
            database(dbfile)
            pilot=root/"pilot"
            now=[BASE]
            def now_fn():return now[0]
            first=run_once(pilot,dbfile,now_fn=now_fn)
            self.assertTrue(first["b3_bootstrapped"])
            self.assertTrue(first["m1_bootstrapped"])
            self.assertEqual(first["quote_requests"],0)
            # Insert a subsequent observation and perform two identical cycles.
            # It fails base filters, so candidate counts must remain 0.
            now[0]=BASE+timedelta(seconds=60)
            with sqlite3.connect(dbfile) as db:
                db.execute("INSERT INTO minute_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",(
                    now[0].isoformat(),"mint","pair",now[0].isoformat(),
                    "LIQUIDITY_UNKNOWN",1.0,None,300,5,2,0.3))
            second=run_once(pilot,dbfile,now_fn=now_fn)
            third=run_once(pilot,dbfile,now_fn=now_fn)
            self.assertEqual(second["b3_added_observations"],1)
            self.assertEqual(third["b3_added_observations"],0)
            self.assertEqual(third["m1_new_candidates"],0)
            self.assertEqual(report(pilot)["successful_cycles"],3)
            self.assertTrue((pilot/"handoff_runs.jsonl").is_file())

    def test_b3_failure_never_calls_m1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            dbfile=root/"minute.sqlite"
            database(dbfile)
            with patch("research_minute_handoff_phase3.b3.execute",side_effect=RuntimeError("bad B3")):
                with patch("research_minute_handoff_phase3.m1.execute") as follow:
                    with self.assertRaisesRegex(RuntimeError,"bad B3"):
                        run_once(root/"pilot",dbfile,now_fn=lambda:BASE)
                    follow.assert_not_called()
            self.assertFalse((root/"pilot"/"handoff_runs.jsonl").exists())

    def test_lock_rejects_parallel_cycle(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            dbfile=root/"minute.sqlite"
            database(dbfile)
            lock=paths_for(root/"pilot")["lock"]
            lock.parent.mkdir(parents=True,exist_ok=True)
            with lock.open("a+") as fd:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                r=run_once(root/"pilot",dbfile,now_fn=lambda:BASE)
                self.assertEqual(r["status"],"SKIPPED_LOCK_HELD")
                self.assertFalse((root/"pilot"/"b3_pilot_state.json").exists())

    def test_pilot_paths_distinct_from_live(self):
        paths=paths_for(Path("/home/ubuntu/gta6-v32-gate-pilot/handoff_phase3_pilot"))
        self.assertTrue(all("pilot" in p.name or p.name in ("handoff_runs.jsonl","handoff.lock") for p in paths.values()))

if __name__=="__main__":
    unittest.main()
