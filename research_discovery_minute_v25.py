"""V2.5: bounded manual 60-second prospective discovery sampling.

Uses existing V2.2 selection/gates with a NEW isolated V2.5 SQLite database.
Runs in foreground for a fixed number of attempts; does not install a timer,
change production databases, submit orders or touch frozen cohorts.
"""
import argparse
import json
import math
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from research_discovery_quality_v22 import execute
from research_discovery_early_activity_v24 import analyse as early_audit

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SHADOW=ROOT/"discovery_v25_minute_shadow.sqlite"
DEFAULT_INTERVAL=60
DEFAULT_CYCLES=10
MAX_CYCLES=30
MAX_ERRORS=3

def window_report(path,started_utc,cycle_rows,errors,interval):
    """Report THIS run, not old cumulative signals in an existing DB."""
    cycles=[r for r in cycle_rows if r.get("status")=="RECORDED"]
    pairs={}
    for arm in ("feed_order","activity_first"):
        pairs[arm]={
            "selected_observations":sum(c[arm]["selected"] for c in cycles),
            "known_liquidity_observations":sum(c[arm]["known_liquidity"] for c in cycles),
            "base_pass_observations":sum(c[arm]["base_pass_including_pair_age"] for c in cycles),
            "momentum_pass_observations":sum(c[arm]["spaced_momentum_pass"] for c in cycles),
            "new_signal_events":sum(c["new_unique_signals"][arm] for c in cycles)
        }
    time_gaps=[]
    for old,new in zip(cycles,cycles[1:]):
        gap=(datetime.fromisoformat(new["observed_utc"])-
             datetime.fromisoformat(old["observed_utc"])).total_seconds()
        time_gaps.append(gap)
    audit=early_audit(path,max_rows=10000,example_limit=2) if Path(path).is_file() else {
        "status":"NO_DATABASE"}
    return {
        "mode":"DISCOVERY_V25_BOUNDED_MINUTE_REPORT",
        "run_started_utc":started_utc,
        "attempted":len(cycle_rows)+len(errors),
        "recorded_cycles":len(cycles),
        "interval_target_seconds":interval,
        "observed_start_gap_seconds":time_gaps,
        "gaps_over_600_seconds":sum(g>600 for g in time_gaps),
        "cohorts":pairs,
        "early_activity_audit":audit,
        "errors":errors,
        "isolated_db":str(path),
        "safety":{"production_writes":0,"orders":0,"timers_created":0},
        "limitations":[
            "Cycle gaps describe successful samples, not gaps for every exact token pair.",
            "V2.2 gates/rolling 5m momentum are research proxies, not execution approval.",
            "A 60-second collector does not guarantee a token remains in the same selected cohort.",
            "Even short successive rolling 5-minute windows overlap.",
            "Early watch-only indicators require prospective validation against independent outcomes."
        ]
    }

def run(path=SHADOW,cycles=DEFAULT_CYCLES,interval=DEFAULT_INTERVAL,slots=20,
        sample_fn=execute,sleep_fn=time.sleep,monotonic_fn=time.monotonic,
        utcnow_fn=lambda:datetime.now(timezone.utc)):
    if not 2<=cycles<=MAX_CYCLES:
        raise ValueError("cycles must be 2..30")
    if not 60<=interval<=300 or not math.isfinite(interval):
        raise ValueError("interval must be 60..300 seconds")
    if not 1<=slots<=20:
        raise ValueError("slots must be 1..20")
    started=utcnow_fn().isoformat(timespec="seconds")
    start_clock=monotonic_fn()
    completed=[]
    errors=[]
    successive_errors=0
    for n in range(cycles):
        deadline=start_clock+n*interval
        remaining=deadline-monotonic_fn()
        if remaining>0:sleep_fn(remaining)
        # If HTTP requests or local runtime exceed interval, do not schedule
        # burst catch-up calls; anchor next attempt at >=60sec after last start.
        call_start=monotonic_fn()
        try:
            result=sample_fn(shadow=path,now=utcnow_fn(),slots=slots)
            if result.get("status") not in ("RECORDED","DUPLICATE_SECOND_SKIPPED"):
                raise RuntimeError("Unexpected sample status: "+str(result.get("status")))
            completed.append(result)
            successive_errors=0
        except (OSError,ValueError,RuntimeError,sqlite3.Error,Exception) as exc:
            errors.append({"attempt":n+1,"type":type(exc).__name__,
                           "message":str(exc)[:300]})
            successive_errors+=1
            if successive_errors>=MAX_ERRORS:break
        # Avoid compressed attempts when slow calls push us past target time.
        current=monotonic_fn()
        lag=current-(start_clock+(n+1)*interval)
        if lag>0:
            start_clock=current-n*interval
        elif current-call_start<0:
            raise RuntimeError("Non-monotonic clock")
    return window_report(path,started,completed,errors,interval)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=str(SHADOW))
    parser.add_argument("--cycles",type=int,default=10)
    parser.add_argument("--interval",type=float,default=60)
    parser.add_argument("--slots",type=int,default=20)
    args=parser.parse_args()
    report=run(path=Path(args.db),cycles=args.cycles,
               interval=args.interval,slots=args.slots)
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
