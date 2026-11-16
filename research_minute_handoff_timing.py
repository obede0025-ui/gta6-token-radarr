"""Read-only systemd timing comparison for the isolated minute research pipeline.

No service changes, timers, API calls, trading, state writes or database access.
Accepts journalctl -o short-iso output through stdin or a local text file.
Measures *service scheduling*, not token discovery or executable quote latency.
"""
import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

B3="gta6-b3-minute-safety-shadow.service"
M1="gta6-m1-minute-research.service"
EVENT=re.compile(r"^(\S+)\s+\S+\s+systemd\[\d+\]:\s+(.*)$")
UNIT=re.compile(r"(gta6-(?:b3-minute-safety-shadow|m1-minute-research)\.service)")
START=re.compile(r"^Starting\s+")
FINISH=re.compile(r"^Finished\s+")

def parse_timestamp(raw):
    try:
        ts=datetime.fromisoformat(raw.replace("Z","+00:00"))
        return ts.astimezone(timezone.utc) if ts.tzinfo else None
    except (ValueError,TypeError,OverflowError):
        return None

def parse_journal(lines):
    """Find systemd run starts and finishes, ignoring application JSON and noise."""
    events=[]
    for line in lines:
        match=EVENT.match(line.strip())
        if not match:
            continue
        ts=parse_timestamp(match.group(1))
        msg=match.group(2)
        unit=UNIT.search(msg)
        if not ts or not unit:
            continue
        action="start" if START.match(msg) else "finish" if FINISH.match(msg) else None
        if action:
            events.append((ts,unit.group(1),action))
    return sorted(events,key=lambda x:x[0])

def complete_runs(events,unit):
    active=None
    runs=[]
    errors=0
    for ts,u,action in events:
        if u!=unit:
            continue
        if action=="start":
            if active is not None:
                errors+=1
            active=ts
        elif action=="finish":
            if active is None or ts<active:
                errors+=1
                continue
            runs.append((active,ts))
            active=None
    if active is not None:
        errors+=1
    return runs,errors

def percentile_nearest_rank(values,p):
    if not values:
        return None
    values=sorted(values)
    import math
    return values[max(0,math.ceil(len(values)*p)-1)]

def stats(values):
    if not values:
        return {"n":0,"median_s":None,"p90_s":None,"max_s":None}
    return {"n":len(values),"median_s":round(statistics.median(values),3),
            "p90_s":round(percentile_nearest_rank(values,.9),3),
            "max_s":round(max(values),3)}

def analyze(lines):
    events=parse_journal(lines)
    b3,b3_errors=complete_runs(events,B3)
    m1,m1_errors=complete_runs(events,M1)
    # Only compare cycles where a B3 run finished before the next M1 start.
    paired=[]
    unmatched=0
    for i,(start,finish) in enumerate(b3):
        next_b3_start=b3[i+1][0] if i+1<len(b3) else None
        eligible=[run for run in m1 if run[0]>=finish and
                  (next_b3_start is None or run[0]<next_b3_start)]
        if not eligible:
            unmatched+=1
            continue
        mstart,mfinish=eligible[0]
        paired.append({"b3_finished_utc":finish.isoformat(),
                       "m1_started_utc":mstart.isoformat(),
                       "m1_finished_utc":mfinish.isoformat(),
                       "gap_seconds":round((mstart-finish).total_seconds(),3),
                       "m1_duration_seconds":round((mfinish-mstart).total_seconds(),3)})
    gaps=[p["gap_seconds"] for p in paired]
    return {"mode":"READ_ONLY_MINUTE_HANDOFF_TIMING_AUDIT",
            "b3_complete_runs":len(b3),"m1_complete_runs":len(m1),
            "incomplete_or_duplicate_events":{"b3":b3_errors,"m1":m1_errors},
            "paired_cycles":len(paired),"unpaired_b3_cycles":unmatched,
            "observed_b3_finish_to_m1_start":stats(gaps),
            "counterfactual_immediate_handoff":{
                "assumed_trigger_delay_seconds":0,
                "avoidable_scheduler_gap_upper_bound":stats(gaps),
                "interpretation":"Best-case reduction in service scheduling wait only; not a measured end-to-end signal speed-up.",
                "requires":"B3 success, idempotent M1, mutual exclusion and no already-running M1"},
            "pairs":paired,
            "limitations":[
                "systemd short-iso logs have second-level timestamps; subsecond execution cannot be inferred.",
                "Independent timers can shift phase; paired cycles are only observed ordered runs.",
                "A simulated immediate trigger is not an actual executed comparison.",
                "No market timestamps, signal arrivals, B3/M1 freshness, API latency or token returns assessed.",
                "No systemd units, original databases, scanner states or quotes are changed.",
                "Missing/incomplete journal history cannot be treated as successful runs."]}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input",help="Journal text file. Default: stdin.")
    ap.add_argument("--summary-only",action="store_true")
    args=ap.parse_args()
    if args.input:
        result=analyze(Path(args.input).read_text(encoding="utf-8").splitlines())
    else:
        result=analyze(sys.stdin)
    if args.summary_only:
        result.pop("pairs")
    print(json.dumps(result,indent=2))
    if not result["paired_cycles"]:
        raise SystemExit(2)

if __name__=="__main__":
    main()
