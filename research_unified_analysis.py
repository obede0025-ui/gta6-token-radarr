"""One-command read-only diagnostic of GTA6 discovery, safety and handoff.

Prints one compact JSON report. Does NOT run new scans, make HTTP calls,
change databases, place orders or reset frozen prospective cohorts.
"""
import argparse
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
MINUTE=ROOT/"minute_market_pilot.sqlite"
V1=ROOT/"discovery_shadow_v1.sqlite"
V2=ROOT/"discovery_shadow_v2.sqlite"
PHASE3=ROOT/"handoff_phase3_pilot"/"handoff_runs.jsonl"

def select_ro(path,query,params=()):
    if not Path(path).is_file():
        return None
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(query,params).fetchall()

def percentage(n,d):
    return round(100*n/d,1) if d else None

def minute_report(path):
    rows=select_ro(path,"""SELECT status,COUNT(*) FROM
       (SELECT status FROM minute_observations ORDER BY observed_at DESC LIMIT 3000)
       GROUP BY status""")
    if rows is None:return {"status":"MISSING"}
    totals=dict(rows);n=sum(totals.values())
    return {"status":"OK","last_observations":n,
            "statuses":totals,
            "unknown_pct":percentage(totals.get("LIQUIDITY_UNKNOWN",0),n)}

def v1_report(path):
    rows=select_ro(path,"""SELECT observed_at,baseline_selected,expanded_selected,
        baseline_known,expanded_known,baseline_base,expanded_base,
        baseline_momentum,expanded_momentum
        FROM discovery_cycles ORDER BY observed_at DESC LIMIT 1""")
    if rows is None:return {"status":"MISSING"}
    if not rows:return {"status":"EMPTY"}
    latest=rows[0]
    totals=select_ro(path,"""SELECT COUNT(*),COALESCE(SUM(baseline_base),0),
        COALESCE(SUM(expanded_base),0),COALESCE(SUM(baseline_momentum),0),
        COALESCE(SUM(expanded_momentum),0) FROM discovery_cycles""")[0]
    signals=select_ro(path,"SELECT COUNT(*) FROM discovery_signals")[0][0]
    return {"status":"OK","cycles":totals[0],"last_cycle_utc":latest[0],
            "latest":{"baseline_selected":latest[1],"expanded_selected":latest[2],
                      "baseline_known":latest[3],"expanded_known":latest[4],
                      "baseline_base":latest[5],"expanded_base":latest[6],
                      "baseline_momentum":latest[7],"expanded_momentum":latest[8]},
            "cumulative_base_observations":{"baseline":totals[1],"expanded":totals[2]},
            "cumulative_momentum_observations":{"baseline":totals[3],"expanded":totals[4]},
            "distinct_signal_pairs":signals}

def blockers(record):
    """Multiple diagnostics per sample; NOT mutually exclusive."""
    _,_,_,cohort,_,price,liq,vol,buys,sells,age,status,base,mom,reason=record
    flags=[]
    if price is None or price<=0: flags.append("PRICE_MISSING_OR_INVALID")
    if liq is None:flags.append("LIQUIDITY_UNKNOWN")
    elif liq<20000:flags.append("LIQUIDITY_BELOW_20000")
    if vol is None:flags.append("VOLUME_UNKNOWN")
    elif vol<250:flags.append("VOLUME_BELOW_250")
    if buys is None or sells is None:flags.append("BUY_SELL_UNKNOWN")
    elif buys<=sells:flags.append("BUY_PRESSURE_WEAK")
    if age is None:flags.append("PAIR_AGE_UNKNOWN")
    elif age<0 or age>24:flags.append("PAIR_AGE_OUTSIDE_0_24H")
    if not mom:flags.append("MOMENTUM_"+str(reason))
    return flags

def quality_report(samples):
    bycohort={}
    for cohort in ("BASELINE","DISCOVERY"):
        subset=[r for r in samples if r[3]==cohort]
        n=len(subset)
        multi=Counter(flag for r in subset for flag in blockers(r))
        # Keep market-base failures separate from proxy momentum diagnostics.
        base_blockers={k:v for k,v in multi.items() if not k.startswith("MOMENTUM_")}
        why=Counter(str(r[14]) for r in subset if r[12] and not r[13])
        bycohort[cohort]={"samples":n,
            "unique_mints":len({r[1] for r in subset}),
            "unique_mints_base_pass":len({r[1] for r in subset if r[12]}),
            "unique_mints_momentum_pass":len({r[1] for r in subset if r[13]}),
            "base_pass_observations":sum(r[12] for r in subset),
            "momentum_pass_observations":sum(r[13] for r in subset),
            "base_blockers_overlapping":dict(sorted(base_blockers.items(), key=lambda item: (-item[1], item[0]))),
            "base_pass_but_not_momentum_reasons":dict(why.most_common())}
    return bycohort

def v2_report(path,sample_limit=3000):
    rows=select_ro(path,"""SELECT observed_at,mint,pair,cohort,feed,price_usd,
      liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours,status,
      base_pass,momentum_pass,momentum_reason
      FROM v2_samples ORDER BY observed_at DESC LIMIT ?""",(sample_limit,))
    if rows is None:return {"status":"MISSING"}
    if not rows:return {"status":"EMPTY"}
    cycles=select_ro(path,"""SELECT COUNT(*),MAX(observed_at),COALESCE(SUM(newly_unique_discovery_signals),0)
        FROM v2_cycles""")[0]
    signals=dict(select_ro(path,"SELECT cohort,COUNT(*) FROM v2_signals GROUP BY cohort"))
    recent_cycle=rows[0][0]
    latest=[r for r in rows if r[0]==recent_cycle]
    return {"status":"OK","cycles":cycles[0],
            "last_cycle_utc":cycles[1],
            "new_discovery_signal_events":cycles[2],
            "unique_signals_by_cohort":signals,
            "latest_cycle_quality":quality_report(latest),
            "recent_sample_quality":quality_report(rows),
            "sample_window_records":len(rows),
            "note":"Rejection reasons overlap. Momentum checks apply only after base passes."}

def phase3_report(path):
    if not Path(path).is_file():return {"status":"MISSING"}
    valid=[]
    errors=0
    with Path(path).open(encoding="utf-8") as f:
        for raw in f:
            try:r=json.loads(raw)
            except (ValueError,TypeError):
                errors+=1;continue
            if r.get("status")=="SUCCESS" and r.get("mode")=="PHASE3_ISOLATED_EVENT_DRIVEN_PILOT":
                valid.append(r)
    gaps=[r.get("b3_to_m1_start_ms") for r in valid
          if isinstance(r.get("b3_to_m1_start_ms"),(int,float))]
    return {"status":"OK","successful_cycles":len(valid),
            "median_internal_handoff_ms":round(statistics.median(gaps),3) if gaps else None,
            "new_b3_candidates":sum(int(r.get("b3_new_candidates") or 0) for r in valid),
            "new_m1_candidates":sum(int(r.get("m1_new_candidates") or 0) for r in valid),
            "malformed_lines":errors,
            "internal_handoff_not_launch_to_signal_latency":True}

def evaluate(data):
    v2=data["discovery_v2"]
    if v2.get("status")!="OK":
        return {"priority":"CHECK_V2_DATA","reason":"No V2 samples yet"}
    a=v2["recent_sample_quality"]["BASELINE"]
    b=v2["recent_sample_quality"]["DISCOVERY"]
    if b["unique_mints_momentum_pass"]>0:
        return {"priority":"VERIFY_NEW_UNIQUE_V2_SIGNAL_AGAINST_B3",
                "reason":"V2 observed research-proxy momentum, not trade approval"}
    reason=b["base_blockers_overlapping"]
    return {"priority":"IMPROVE_QUALIFIED_DISCOVERY_NOT_RAW_SCAN_COUNT",
            "reason":"No qualifying V2 momentum in inspected samples",
            "largest_discovery_base_blockers":dict(Counter(reason).most_common(4)),
            "next_action":"Collect prospective spaced V2 samples; audit liquidity, pool age, volume and buy pressure before changing filters"}

def analyse(minute=MINUTE,v1=V1,v2=V2,phase3=PHASE3,limit=3000):
    if not 1<=limit<=100000:
        raise ValueError("limit must be 1..100000")
    sections={}
    for name,fn,path in (
        ("minute_market",minute_report,minute),
        ("discovery_v1",v1_report,v1),
        ("discovery_v2",lambda p:v2_report(p,limit),v2),
        ("phase3_handoff",phase3_report,phase3)):
        try:sections[name]=fn(path)
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError) as exc:
            sections[name]={"status":"ERROR","detail":type(exc).__name__+": "+str(exc)[:160]}
    return {"mode":"GTA6_UNIFIED_READ_ONLY_ANALYSIS",
            "generated_utc":datetime.now(timezone.utc).isoformat(),
            **sections,"recommendation":evaluate(sections),
            "safety":{"all_source_databases_read_only":True,
                      "http_requests":0,"orders":0,"production_changes":0,
                      "does_not_reset_frozen_cohorts":True},
            "limitations":[
                "V1 and V2 momentum are rolling-volume research proxies, not full B3.",
                "Cumulative observations are not independent or unique tokens.",
                "V2 feed boosts can be paid promotion and do not represent all launches.",
                "No execution, safety, profitability or launch-to-discovery validation."]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--minute",default=str(MINUTE))
    p.add_argument("--v1",default=str(V1))
    p.add_argument("--v2",default=str(V2))
    p.add_argument("--phase3",default=str(PHASE3))
    p.add_argument("--sample-limit",type=int,default=3000)
    args=p.parse_args()
    result=analyse(args.minute,args.v1,args.v2,args.phase3,args.sample_limit)
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=="__main__":main()
