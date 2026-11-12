"""Forward-only minute-market B3 safety shadow. NEVER generates trade approvals.

Consumes the *isolated* minute_market_pilot.sqlite; keeps an independent
JSON cursor/state. B3's legacy momentum rule expects 3.5-20-minute spacing.
We do NOT claim its equivalence on one-minute sampling, and do not emit
REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW. No orders or external API calls.
"""
import argparse
import fcntl
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DB_DEFAULT = "/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
STATE_DEFAULT = "/home/ubuntu/gta6-v32-gate-pilot/b3_minute_safety_shadow_state.json"
MAX_EVENTS=10000
MAX_AGE_SECONDS=180

def parsed(s):
    try:
        d=datetime.fromisoformat(str(s).replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,TypeError,OverflowError): return None

def num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError): return None

def key(row):
    return [row[0],row[1],row[2]]

def assess(row):
    t,mint,pair,source_t,status,price,liq,vol,buys,sells,age=row
    reasons=[]
    if status!="QUOTE_MARKET_OBSERVED":
        return {"decision":"MARKET_DATA_UNAVAILABLE","reasons":["MARKET_DATA_UNAVAILABLE"]}
    values=[num(v) for v in (price,liq,vol,buys,sells,age)]
    price,liq,vol,buys,sells,age=values
    if price is None or price<=0: reasons.append("INVALID_PRICE")
    if liq is None or liq<20000: reasons.append("LIQUIDITY_BELOW_20000")
    if vol is None or vol<250: reasons.append("VOLUME_BELOW_250")
    if buys is None or sells is None or buys<=sells: reasons.append("BUY_SELL_IMBALANCE")
    if age is None or not 0<=age<=24: reasons.append("PAIR_AGE_INVALID")
    return {"decision":"BASE_FILTER_BLOCKED" if reasons else "BASE_FILTER_PASS_MOMENTUM_UNVERIFIED",
            "reasons":reasons}

def advance(state,db,now):
    if state.get("version")!=1 or state.get("mode")!="MINUTE_B3_SAFETY_SHADOW" or not isinstance(state.get("cursor"),list):
        raise ValueError("Invalid safety shadow state")
    cur=state["cursor"]
    rows=db.execute("""
        SELECT observed_at,mint,pair,source_snapshot_at,status,price_usd,
               liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
        FROM minute_observations
        WHERE observed_at>=?
        ORDER BY observed_at,mint,pair
    """,(cur[0],)).fetchall()
    fresh=[]
    skipped=0
    for row in rows:
        k=key(row)
        if k<=state["cursor"]:continue
        state["cursor"]=k
        ts=parsed(row[0])
        delta=(now-ts).total_seconds() if ts else None
        if delta is None or delta<0 or delta>MAX_AGE_SECONDS:
            skipped+=1
            continue
        result=assess(row)
        fresh.append({
            "observed_at":row[0],"mint":row[1],"pair":row[2],
            "source_snapshot_at":row[3],
            "decision":result["decision"],"reasons":result["reasons"],
            "observed_utc":now.isoformat(),
        })
    state["events"]=(state["events"]+fresh)[-MAX_EVENTS:]
    state["total_logged"]=state.get("total_logged",0)+len(fresh)
    state["last_checked_utc"]=now.isoformat()
    return {"added":len(fresh),"stale_skipped":skipped,
            "cursor":state["cursor"],"total_logged":state["total_logged"],
            "new_decisions":dict(Counter(e["decision"] for e in fresh)),
            "last_checked_utc":state["last_checked_utc"],
            "note":"BASE_FILTER_PASS is NOT full B3 approval; momentum not evaluated."}

def bootstrap(db,now):
    last=db.execute("""
        SELECT observed_at,mint,pair FROM minute_observations
        ORDER BY observed_at DESC,mint DESC,pair DESC LIMIT 1
    """).fetchone()
    return {"version":1,"mode":"MINUTE_B3_SAFETY_SHADOW",
            "started_utc":now.isoformat(),"cursor":list(last) if last else ["","",""],
            "events":[],"total_logged":0,
            "limitations":[
                "Only baseline B3 checks, NOT the legacy signal_b momentum rule.",
                "No trade-approved token can be emitted from this observer.",
                "No historical replay before the first run.",
                "Missing market data is UNKNOWN rather than low liquidity.",
                "Observations are restricted to exact pinned pairs, not best alternate routes.",
                "Original collector, B3, M1, V3.2 and learning.db are untouched."
            ]}

def execute(dbpath,statepath,now=None):
    now=now or datetime.now(timezone.utc)
    target=Path(statepath)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.with_name(target.name+".lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with sqlite3.connect(Path(dbpath).resolve().as_uri()+"?mode=ro",uri=True) as db:
            if not target.exists():
                state=bootstrap(db,now)
                with target.open("x") as f:
                    json.dump(state,f,indent=2)
                return {"bootstrapped":True,"note":"Existing minute observations excluded"}
            state=json.loads(target.read_text())
            result=advance(state,db,now)
        tmp=target.with_name(target.name+".tmp")
        tmp.write_text(json.dumps(state,indent=2)+"\n")
        tmp.replace(target)
        return {"mode":"MINUTE_B3_SAFETY_SHADOW",**result}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=DB_DEFAULT)
    p.add_argument("--state",default=STATE_DEFAULT)
    p.add_argument("--report-only",action="store_true")
    args=p.parse_args()
    if args.report_only:
        state=json.loads(Path(args.state).read_text())
        print(json.dumps({"mode":state["mode"],"total_logged":state["total_logged"],
                          "retained_decisions":dict(Counter(e["decision"] for e in state["events"])),
                          "last_checked_utc":state.get("last_checked_utc"),
                          "limitations":state.get("limitations")},indent=2));return
    print(json.dumps(execute(args.db,args.state)))

if __name__=="__main__":main()
