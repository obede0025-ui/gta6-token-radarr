"""Isolated event-driven B3 -> M1 phase 3 pilot (NO trades / Jupiter calls).

Real B3 and M1 research code, but completely separate JSON states and lock.
The original B3/M1 services, timers, states, quote jobs, and DB remain untouched.
Run manually first; optional timer deployment is a separate, explicit step.
"""
import argparse
import fcntl
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import research_b3_minute_safety_shadow as b3
import research_m1_minute_followup as m1

PILOT_DEFAULT=Path("/home/ubuntu/gta6-v32-gate-pilot/handoff_phase3_pilot")
DB_DEFAULT=Path(b3.DB_DEFAULT)
LIVE_STATES=(Path(b3.STATE_DEFAULT),Path(m1.STATE))

def paths_for(directory):
    directory=Path(directory).resolve()
    # Never allow a live state location to be used, even via symlinks.
    result={"b3":directory/"b3_pilot_state.json",
            "m1":directory/"m1_pilot_state.json",
            "log":directory/"handoff_runs.jsonl",
            "lock":directory/"handoff.lock"}
    for p in result.values():
        resolved=p.resolve()
        if resolved in {x.resolve() for x in LIVE_STATES}:
            raise ValueError("Refusing to use a production state path")
    return result

def run_once(pilot_dir=PILOT_DEFAULT,db_path=DB_DEFAULT,now_fn=None,clock=None):
    # Injection only for deterministic tests; normal operation uses real UTC and monotonic.
    now_fn=now_fn or (lambda: datetime.now(timezone.utc))
    clock=clock or time.perf_counter_ns
    paths=paths_for(pilot_dir)
    paths["lock"].parent.mkdir(parents=True,exist_ok=True)
    with paths["lock"].open("a+") as locked:
        try:
            fcntl.flock(locked,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mode":"PHASE3_ISOLATED_EVENT_DRIVEN_PILOT","status":"SKIPPED_LOCK_HELD",
                    "note":"Another pilot cycle is running; no pilot state was accessed"}
        if not Path(db_path).is_file():
            raise FileNotFoundError(f"Pilot DB absent: {db_path}")
        started=now_fn()
        b3_report=b3.execute(str(db_path),str(paths["b3"]),now=started)
        b3_done_ns=clock()
        # B3 has completed successfully before M1 is invoked.
        m1_started_ns=clock()
        m1_report=m1.execute(str(paths["b3"]),str(db_path),str(paths["m1"]),now=now_fn())
        m1_done_ns=clock()
        result={"mode":"PHASE3_ISOLATED_EVENT_DRIVEN_PILOT",
                "status":"SUCCESS",
                "cycle_started_utc":started.isoformat(),
                "cycle_finished_utc":now_fn().isoformat(),
                "b3_to_m1_start_ms":round((m1_started_ns-b3_done_ns)/1e6,3),
                "m1_execution_ms":round((m1_done_ns-m1_started_ns)/1e6,3),
                "b3_bootstrapped":bool(b3_report.get("bootstrapped")),
                "m1_bootstrapped":bool(m1_report.get("bootstrapped")),
                "b3_added_observations":b3_report.get("added",0),
                "b3_new_candidates":b3_report.get("new_research_candidates",0),
                "m1_new_candidates":m1_report.get("new_candidates",0),
                "pilot_only":True,
                "quote_requests":0,
                "trades":0,
                "limitations":[
                    "Handoff timing is measured within the pilot process, not end-to-end token discovery.",
                    "Pilot and production B3 can consume the same read-only DB with different independent cursors.",
                    "First run bootstraps states and intentionally excludes historical observations.",
                    "No automated trading, Jupiter quotes or production systemd modification.",
                    "A successful pilot does not imply profit or acceptable quote execution."]}
        # Record one summary per completed run; never touch original states.
        with paths["log"].open("a",encoding="utf-8") as out:
            out.write(json.dumps(result,sort_keys=True)+"\n")
            out.flush()
            os.fsync(out.fileno())
        return result

def report(pilot_dir=PILOT_DEFAULT):
    file=paths_for(pilot_dir)["log"]
    rows=[]
    if file.exists():
        for raw in file.read_text(encoding="utf-8").splitlines():
            if not raw.strip():continue
            try:
                row=json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("mode")=="PHASE3_ISOLATED_EVENT_DRIVEN_PILOT" and row.get("status")=="SUCCESS":
                rows.append(row)
    gaps=[r["b3_to_m1_start_ms"] for r in rows]
    return {"mode":"PHASE3_PILOT_READ_ONLY_REPORT",
            "successful_cycles":len(rows),
            "b3_to_m1_start_ms_median":round(statistics.median(gaps),3) if gaps else None,
            "b3_to_m1_start_ms_max":max(gaps) if gaps else None,
            "total_b3_candidates":sum(r.get("b3_new_candidates",0) for r in rows),
            "total_m1_candidates":sum(r.get("m1_new_candidates",0) for r in rows),
            "has_prospective_new_candidates":any(r.get("b3_new_candidates",0)>0 for r in rows),
            "no_live_timer_changes":True,
            "note":"Compare with previously measured 31s baseline as service scheduling only; this is not proof of faster token discovery."}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pilot-dir",default=str(PILOT_DEFAULT))
    p.add_argument("--db",default=str(DB_DEFAULT))
    p.add_argument("--report-only",action="store_true")
    args=p.parse_args()
    result=report(args.pilot_dir) if args.report_only else run_once(args.pilot_dir,args.db)
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
