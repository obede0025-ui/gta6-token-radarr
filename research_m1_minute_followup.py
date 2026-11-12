"""Forward-only M1 research follow-up for the isolated minute B3 pilot.

Consumes B3 research_candidates and pinned-pair minute data, NO APIs, orders,
wallets, fills, Jupiter claims or modifications to original states.
One observation per recorded minute is not continuous market coverage.
"""
import argparse
import fcntl
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

B3="/home/ubuntu/gta6-v32-gate-pilot/b3_minute_safety_shadow_state.json"
DB="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
STATE="/home/ubuntu/gta6-v32-gate-pilot/m1_minute_research_state.json"
HORIZON_SECONDS=900
TP_PCT=10
SL_PCT=-10
MAX_SIGNAL_LAG_SECONDS=180
RULES={"horizon_seconds":HORIZON_SECONDS,"tp_pct":TP_PCT,"sl_pct":SL_PCT,
       "max_signal_lag_seconds":MAX_SIGNAL_LAG_SECONDS,"research_only":True}

def stamp(s):
    if not isinstance(s,str):return None
    try:
        d=datetime.fromisoformat(s.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo is not None else None
    except (ValueError,OverflowError):return None

def finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError):return None

def candidate_id(c):
    return json.dumps([c["mint"],c["pair"],c["signal_utc"]],separators=(",",":"))

def initialize(now,b3):
    if b3.get("mode")!="MINUTE_B3_SAFETY_SHADOW" or not isinstance(b3.get("research_candidates"),list):
        raise ValueError("Invalid B3 source")
    return {"version":1,"mode":"M1_MINUTE_RESEARCH_ONLY",
            "started_utc":now.isoformat(),"rules":RULES,
            "candidates":{},"ignored":{},"last_checked_utc":now.isoformat()}

def validate(state):
    if (state.get("version")!=1 or state.get("mode")!="M1_MINUTE_RESEARCH_ONLY"
        or state.get("rules")!=RULES or not isinstance(state.get("candidates"),dict)
        or not isinstance(state.get("ignored"),dict) or stamp(state.get("started_utc")) is None):
        raise ValueError("Invalid / incompatible M1 state, refusing overwrite")

def observations(db,candidate,now):
    signal=stamp(candidate["signal_utc"])
    end=min(now,signal+timedelta(seconds=HORIZON_SECONDS))
    return db.execute("""
       SELECT observed_at,status,price_usd,liquidity_usd
       FROM minute_observations
       WHERE mint=? AND pair=? AND observed_at>? AND observed_at<=?
       ORDER BY observed_at
    """,(candidate["mint"],candidate["pair"],candidate["signal_utc"],
         end.isoformat(timespec="microseconds"))).fetchall()

def follow(candidate,db,now):
    """Recompute only from samples at/before now; no forward price leakage."""
    entry=finite(candidate["entry_observed_price_usd"])
    if entry is None or entry<=0:raise ValueError("Invalid frozen entry")
    signal=stamp(candidate["signal_utc"])
    valid=[]
    unknown=0
    for ts,status,price,liq in observations(db,candidate,now):
        p=finite(price);q=finite(liq)
        if status=="QUOTE_MARKET_OBSERVED" and p is not None and p>0 and q is not None:
            valid.append((ts,p,(p/entry-1)*100,q))
        else:
            unknown+=1
    result={"status":"TRACKING","valid_followup_samples":len(valid),
            "unknown_followup_samples":unknown,
            "first_threshold_observation":None,
            "last_observed_utc":valid[-1][0] if valid else None,
            "last_spot_change_pct":round(valid[-1][2],4) if valid else None,
            "min_observed_spot_change_pct":round(min(x[2] for x in valid),4) if valid else None,
            "max_observed_spot_change_pct":round(max(x[2] for x in valid),4) if valid else None,
            "exit_spot_proxy_change_pct":None}
    # First observed crossing only; cannot know intra-minute order or fills.
    for ts,p,pct,q in valid:
        if pct>=TP_PCT or pct<=SL_PCT:
            result["status"]="TP_SPOT_OBSERVED" if pct>=TP_PCT else "SL_SPOT_OBSERVED"
            result["first_threshold_observation"]={"utc":ts,"spot_change_pct":round(pct,4)}
            result["exit_spot_proxy_change_pct"]=round(pct,4)
            break
    # Once a threshold was seen, never upgrade to a fill assertion.
    if result["first_threshold_observation"] is None and now>=signal+timedelta(seconds=HORIZON_SECONDS):
        result["status"]="HORIZON_LAST_SPOT_OBSERVED" if valid else "HORIZON_NO_VALID_SPOT"
        if valid:result["exit_spot_proxy_change_pct"]=round(valid[-1][2],4)
    return result

def advance(state,b3,db,now):
    validate(state)
    if b3.get("mode")!="MINUTE_B3_SAFETY_SHADOW" or not isinstance(b3.get("research_candidates"),list):
        raise ValueError("Invalid B3 research source")
    started=stamp(state["started_utc"])
    added=0
    for c in b3["research_candidates"]:
        if not isinstance(c,dict) or c.get("research_only") is not True:
            continue
        if not all(isinstance(c.get(x),str) and c[x] for x in ("mint","pair","signal_utc")):
            continue
        signal=stamp(c["signal_utc"])
        if signal is None:continue
        ident=candidate_id(c)
        if ident in state["candidates"] or ident in state["ignored"]:continue
        # Prospective cohort: never retroactively turn old signals into entries.
        if signal<=started:
            state["ignored"][ident]="PRE_BOOTSTRAP_SIGNAL"
            continue
        if (now-signal).total_seconds()<0:
            continue
        if (now-signal).total_seconds()>MAX_SIGNAL_LAG_SECONDS:
            state["ignored"][ident]="SIGNAL_TOO_OLD_FOR_FRESH_TRACKING"
            continue
        entry=finite(c.get("observed_price_usd"))
        liquidity=finite(c.get("observed_liquidity_usd"))
        if entry is None or entry<=0 or liquidity is None or liquidity<20000:
            state["ignored"][ident]="ENTRY_FEATURE_INVALID_OR_UNSAFE"
            continue
        # Frozen signal-time observed price; NOT Jupiter entry quote.
        state["candidates"][ident]={
            "mint":c["mint"],"pair":c["pair"],"signal_utc":c["signal_utc"],
            "registered_utc":now.isoformat(),
            "entry_observed_price_usd":entry,
            "entry_liquidity_usd":liquidity,
            "entry_volume_m5":finite(c.get("observed_volume_m5")),
            "research_only":True,
            "followup":{}
        }
        added+=1
    for c in state["candidates"].values():
        c["followup"]=follow(c,db,now)
    state["last_checked_utc"]=now.isoformat()
    return added

def summary(state):
    validate(state)
    candidates=list(state["candidates"].values())
    return {"mode":state["mode"],"started_utc":state["started_utc"],
            "last_checked_utc":state.get("last_checked_utc"),
            "tracked":len(candidates),
            "statuses":dict(Counter(c["followup"].get("status","UNASSESSED") for c in candidates)),
            "ignored":dict(Counter(state["ignored"].values())),
            "rules":RULES,
            "limitations":[
                "Entry and exits are sampled Dexscreener spot prices, NOT quotes or executable fills.",
                "TP/SL crossings between minute observations can be missed.",
                "Unknown liquidity never becomes an executable exit.",
                "No net return, spread, slippage, gas or realized PnL is calculated.",
                "Only new B3 research candidates after this pilot bootstrap are tracked.",
                "The B3 spaced momentum variant is research-only, not validated legacy B3 equivalence.",
                "No access to real wallets, orders or original production states."
            ]}

def execute(b3_path,db_path,state_path,now=None):
    now=now or datetime.now(timezone.utc)
    target=Path(state_path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.with_name(target.name+".lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        b3=json.loads(Path(b3_path).read_text())
        if not target.exists():
            state=initialize(now,b3)
            with target.open("x") as f:json.dump(state,f,indent=2)
            return {"bootstrapped":True,"existing_candidates_excluded":len(b3["research_candidates"])}
        state=json.loads(target.read_text())
        with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
            added=advance(state,b3,db,now)
        temp=target.with_name(target.name+".tmp")
        temp.write_text(json.dumps(state,indent=2,ensure_ascii=False)+"\n")
        temp.replace(target)
        return {"new_candidates":added,**summary(state)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--b3",default=B3)
    p.add_argument("--db",default=DB)
    p.add_argument("--state",default=STATE)
    p.add_argument("--report-only",action="store_true")
    args=p.parse_args()
    if args.report_only:
        print(json.dumps(summary(json.loads(Path(args.state).read_text())),indent=2));return
    print(json.dumps(execute(args.b3,args.db,args.state),ensure_ascii=False))

if __name__=="__main__":main()
