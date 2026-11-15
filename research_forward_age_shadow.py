"""Frozen, prospective quote-only comparison: 3% gate vs 3% + 30m pair age.

Initialize once; it writes only an isolated epoch marker using exclusive create.
Reports only signals and quote entries occurring AFTER that epoch. Read-only
report draws on already observed Jupiter quotes and strictly prior exact-pair
market samples. No new timers, HTTP requests, trading or state modifications.

Age data are recorded separately at market sample time, but strict timestamp
precedence alone cannot prove API publication/ingestion latency. No fills.
"""
import argparse
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_jupiter_minute_quotes import STATE, dt
from research_jupiter_minute_evaluation import evaluate, HORIZON
from research_pre_signal_age_sensitivity import strictly_prior
from research_pre_signal_risk import MINUTE, ORIGINAL, ro

EPOCH="/home/ubuntu/gta6-v32-gate-pilot/forward_age_30m_epoch.json"
MIN_AGE_MINUTES=30
MIN_SAMPLES_TO_REVIEW=30

def initialize_epoch(path=EPOCH, now=None):
    now=now or datetime.now(timezone.utc)
    marker={"mode":"FROZEN_FORWARD_AGE_COMPARISON","version":1,
            "started_utc":now.isoformat(timespec="microseconds"),
            "baseline_roundtrip_max_loss_pct":3.0,
            "shadow_minimum_pair_age_minutes":MIN_AGE_MINUTES,
            "jupiter_horizon_seconds":HORIZON,
            "paper_only":True}
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    # Deliberately refuse reset; a new start after observing results would
    # contaminate the prospective holdout.
    with target.open("x",encoding="utf-8") as f:
        json.dump(marker,f,indent=2)
        f.write("\n")
    return marker

def load_epoch(path):
    marker=json.loads(Path(path).read_text(encoding="utf-8"))
    if (marker.get("mode")!="FROZEN_FORWARD_AGE_COMPARISON"
        or marker.get("version")!=1
        or marker.get("baseline_roundtrip_max_loss_pct")!=3.0
        or marker.get("shadow_minimum_pair_age_minutes")!=MIN_AGE_MINUTES
        or marker.get("jupiter_horizon_seconds")!=HORIZON
        or dt(marker.get("started_utc")) is None):
        raise ValueError("Invalid or altered research epoch; refusing report")
    return marker

def prior_age(minute,original,position,signal):
    mint,pair=position.get("mint"),position.get("pair")
    a=strictly_prior(minute,"minute_observations",mint,pair,signal)
    b=strictly_prior(original,"snapshots",mint,pair,signal)
    choices=[(dt(x["recorded_utc"]),rank,source,x) for rank,source,x
             in ((1,"minute",a),(0,"original",b)) if x]
    if not choices:return {"status":"UNKNOWN","source":None,"age_minutes":None}
    _,_,source,market=max(choices,key=lambda item:(item[0],item[1]))
    age=market.get("pair_age_hours")
    if isinstance(age,bool) or not isinstance(age,(float,int)) or not math.isfinite(age) or age<0:
        return {"status":"UNKNOWN","source":source,"age_minutes":None}
    return {"status":"KNOWN","source":source,
            "age_minutes":round(age*60,4),
            "sample_lag_seconds":market["lag_seconds"],
            "sample_utc":market["recorded_utc"]}

def classify(position, epoch, now, minute, original):
    signal=dt(position.get("signal_utc"))
    entry=dt(position.get("entry_quote_utc"))
    if signal is None or entry is None or signal<=epoch or entry<=epoch:
        return None
    q=evaluate(position)
    age=prior_age(minute,original,position,signal)
    matured=now>=entry+timedelta(seconds=HORIZON+120)
    outcome=q.get("outcome")
    if not matured:
        outcome="PENDING_HORIZON"
    return {"mint":position.get("mint"),"signal_utc":signal.isoformat(),
            "entry_utc":entry.isoformat(),"gate":q.get("gate"),
            "age_status":age["status"],"age_source":age["source"],
            "pre_signal_age_minutes":age["age_minutes"],
            "pre_signal_sample_lag_seconds":age.get("sample_lag_seconds"),
            "matured":matured,"outcome":outcome,
            "stress_quote_pct":q.get("stress_quote_pct") if matured else None}

def aggregate(rows):
    outcomes=Counter(r["outcome"] for r in rows)
    observed=sorted(r["stress_quote_pct"] for r in rows
                    if r["stress_quote_pct"] is not None and r["matured"])
    return {"candidates":len(rows),"matured":sum(r["matured"] for r in rows),
            "pending":sum(not r["matured"] for r in rows),
            "outcomes":dict(outcomes),"observable_exits":len(observed),
            "unobserved_matured":sum(r["matured"] for r in rows)-len(observed),
            "observed_mean_stress_pct":round(sum(observed)/len(observed),4) if observed else None,
            "observed_worst_stress_pct":observed[0] if observed else None}

def compare(rows):
    baseline=[r for r in rows if r["gate"]=="PASS_ROUNDTRIP_AT_MOST_3_PERCENT"]
    aged=[r for r in baseline if r["age_status"]=="KNOWN"
          and r["pre_signal_age_minutes"]>=MIN_AGE_MINUTES]
    younger=[r for r in baseline if r["age_status"]=="KNOWN"
             and r["pre_signal_age_minutes"]<MIN_AGE_MINUTES]
    unknown=[r for r in baseline if r["age_status"]!="KNOWN"]
    blocked=[r for r in rows if r["gate"]=="BLOCKED_ROUNDTRIP_OVER_3_PERCENT"]
    no_gate=[r for r in rows if r["gate"] not in (
        "PASS_ROUNDTRIP_AT_MOST_3_PERCENT","BLOCKED_ROUNDTRIP_OVER_3_PERCENT")]
    return {"all_new_quote_candidates":aggregate(rows),
            "A_3pct_gate":aggregate(baseline),
            "B_3pct_plus_30min":aggregate(aged),
            "B_excluded_younger":aggregate(younger),
            "B_unknown_age_not_admitted":aggregate(unknown),
            "blocked_by_3pct":aggregate(blocked),
            "unclassified_roundtrip":aggregate(no_gate),
            "outcome_pairwise_counts":{
                "A_tp":sum(r["matured"] and r["outcome"]=="TP_QUOTE_OBSERVED" for r in baseline),
                "A_sl":sum(r["matured"] and r["outcome"]=="SL_QUOTE_OBSERVED" for r in baseline),
                "B_tp":sum(r["matured"] and r["outcome"]=="TP_QUOTE_OBSERVED" for r in aged),
                "B_sl":sum(r["matured"] and r["outcome"]=="SL_QUOTE_OBSERVED" for r in aged)},
            "eligible_new_positions":len(rows)}

def report(epoch_path=EPOCH,state_path=STATE,minute_path=MINUTE,
           original_path=ORIGINAL,now=None):
    now=now or datetime.now(timezone.utc)
    marker=load_epoch(epoch_path)
    epoch=dt(marker["started_utc"])
    state=json.loads(Path(state_path).read_text(encoding="utf-8"))
    if state.get("mode")!="JUPITER_MINUTE_QUOTE_ONLY" or not isinstance(state.get("positions"),dict):
        raise ValueError("Invalid Jupiter quote state")
    with ro(minute_path) as minute,ro(original_path) as original:
        rows=[classify(position,epoch,now,minute,original)
              for position in state["positions"].values() if isinstance(position,dict)]
    rows=[r for r in rows if r is not None]
    data=compare(rows)
    return {"mode":"FORWARD_ONLY_FROZEN_AGE_30M_QUOTE_RESEARCH",
            "epoch_utc":marker["started_utc"],
            "checked_utc":now.isoformat(),"elapsed_hours":round((now-epoch).total_seconds()/3600,2),
            **data,"verdict":"INSUFFICIENT_FORWARD_SAMPLE" if len(rows)<MIN_SAMPLES_TO_REVIEW
                     else "DESCRIPTIVE_FORWARD_OBSERVATIONS_ONLY",
            "limitations":[
                "Threshold fixed at 30m before all included signals, epoch cannot be reset.",
                "Both variants compare the same Jupiter quotes; no additional route or buy/sell requests.",
                "Only candidates entering the existing minute M1/Jupiter pipeline are observable.",
                "A signal that never reaches Jupiter is not covered by this test.",
                "Source timestamp strictly predates signal; ingestion/publication availability cannot be fully established.",
                "Pair age comes from exact DexScreener pair, Jupiter may route a different liquidity pool.",
                "Outcome only shown as final when the 900s horizon plus 120s grace has elapsed.",
                "Censored and missing quotes are never scored as winners, losses or zero.",
                "No executable fills, trading P&L, slippage guarantees or automatic parameter promotion.",
                "No API calls, writes to original DB, timers or active scanner changes."
            ]}

def main():
    p=argparse.ArgumentParser()
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--initialize",action="store_true",
                      help="Create the one-time immutable future research epoch")
    mode.add_argument("--report",action="store_true",
                      help="Read-only comparison of post-epoch signals")
    p.add_argument("--epoch",default=EPOCH)
    p.add_argument("--state",default=STATE)
    p.add_argument("--minute",default=MINUTE)
    p.add_argument("--original",default=ORIGINAL)
    args=p.parse_args()
    if args.initialize:
        result=initialize_epoch(args.epoch)
    else:
        result=report(args.epoch,args.state,args.minute,args.original)
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__":main()
