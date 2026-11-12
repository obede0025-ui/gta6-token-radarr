"""Jupiter Swap V2 quote-only monitor for NEW M1 minute research candidates.

No taker/wallet, no POST, no transaction generation, no orders or fills.
Buy quote USDC -> token for 25 USDC; exit quotes reverse the fixed raw token
amount from the buy quote. All quotes are prospective and stored in separate
state. The quoted token amount is not an actual position.
"""
import argparse
import fcntl
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

M1="/home/ubuntu/gta6-v32-gate-pilot/m1_minute_research_state.json"
STATE="/home/ubuntu/gta6-v32-gate-pilot/jupiter_minute_quotes_state.json"
API="https://api.jup.ag/swap/v2/order"
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
STAKE_RAW=25_000_000
HORIZON_SECONDS=900
MAX_ENTRY_LAG_SECONDS=180
MAX_ACTIVE=2
MAX_REQUESTS_PER_CYCLE=4
REQUEST_SPACING_SECONDS=2.2
RULES={"entry_usdc":25,"input_usdc_raw":STAKE_RAW,"horizon_seconds":HORIZON_SECONDS,
       "max_entry_lag_seconds":MAX_ENTRY_LAG_SECONDS,
       "max_active":MAX_ACTIVE,"max_requests_per_cycle":MAX_REQUESTS_PER_CYCLE,
       "quote_only":True,"swap_api":"v2/order","no_taker":True}

def dt(s):
    try:
        d=datetime.fromisoformat(s.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,AttributeError,OverflowError):return None

def amount(s):
    if isinstance(s,bool):return None
    try:
        v=int(s)
        return v if v>0 and str(v)==str(s) else None
    except (TypeError,ValueError,OverflowError):return None

def validate_response(payload,expected_input,expected_output,requested):
    if not isinstance(payload,dict):return {"status":"QUOTE_INVALID_RESPONSE"}
    if payload.get("errorCode") is not None or payload.get("errorMessage"):
        return {"status":"QUOTE_NO_ROUTE","error_code":payload.get("errorCode")}
    if payload.get("transaction") not in (None,""):
        return {"status":"QUOTE_NOT_QUOTE_ONLY"}
    incoming=amount(payload.get("inAmount"))
    outgoing=amount(payload.get("outAmount"))
    if incoming!=requested or outgoing is None:
        return {"status":"QUOTE_INVALID_AMOUNTS"}
    # Some V2 responses may omit token identifiers. If present, check them.
    if payload.get("inputMint",expected_input)!=expected_input or payload.get("outputMint",expected_output)!=expected_output:
        return {"status":"QUOTE_MINT_MISMATCH"}
    return {"status":"QUOTE_AVAILABLE","in_raw":incoming,"out_raw":outgoing,
            "router":payload.get("router"),"fee_bps":payload.get("feeBps")}

def get_quote(session,input_mint,output_mint,raw,api_key):
    headers={"Accept":"application/json"}
    if api_key:headers["x-api-key"]=api_key
    try:
        response=session.get(API,params={"inputMint":input_mint,
                                         "outputMint":output_mint,
                                         "amount":str(raw)},
                             headers=headers,timeout=15)
        if response.status_code!=200:
            return {"status":"QUOTE_HTTP_ERROR","http_status":response.status_code}
        return validate_response(response.json(),input_mint,output_mint,raw)
    except (requests.RequestException,ValueError):
        return {"status":"QUOTE_REQUEST_ERROR"}

def initialize(now,m1):
    if m1.get("mode")!="M1_MINUTE_RESEARCH_ONLY" or not isinstance(m1.get("candidates"),dict):
        raise ValueError("Invalid M1 state")
    return {"version":1,"mode":"JUPITER_MINUTE_QUOTE_ONLY","started_utc":now.isoformat(),
            "rules":RULES,"positions":{},"skipped":{},"last_checked_utc":now.isoformat()}

def validate(state):
    if (state.get("version")!=1 or state.get("mode")!="JUPITER_MINUTE_QUOTE_ONLY"
        or state.get("rules")!=RULES or not isinstance(state.get("positions"),dict)
        or not isinstance(state.get("skipped"),dict) or dt(state.get("started_utc")) is None):
        raise ValueError("Incompatible existing Jupiter quote state; refusing overwrite")

def count_active(state,now):
    return sum(1 for p in state["positions"].values()
               if p.get("entry_token_raw") and
               (dt(p.get("entry_quote_utc")) is not None) and
               (now-dt(p["entry_quote_utc"])).total_seconds()<HORIZON_SECONDS)

def summary(state):
    validate(state)
    positions=state["positions"]
    return {"mode":state["mode"],"started_utc":state["started_utc"],
            "last_checked_utc":state.get("last_checked_utc"),"positions":len(positions),
            "entry_quotes":sum(p.get("entry_token_raw") is not None for p in positions.values()),
            "outcomes":dict(Counter(
                (p.get("exit_quotes") or [{}])[-1].get("status","NO_EXIT_QUOTE")
                for p in positions.values())),
            "skipped":dict(Counter(state["skipped"].values())),
            "rules":RULES,
            "notes":[
                "Spot price B3/M1 signals and Jupiter quotes are separate; quote time is the entry baseline.",
                "Quoted token amount is hypothetical; no buy/sell fills occurred.",
                "Exit quotes are USDC output for the raw token amount from the entry quote.",
                "Swap V2 /order is called without taker (transaction absent). No execute/swap POST calls.",
                "Quote output is not guaranteed sellability, settlement, or net PnL.",
                "Priority fees, gas, failed execution, transfer taxes and slippage are not modeled.",
                "Quote-only API responses are stored without full transaction blobs.",
                "Only candidates registered after quote-pilot bootstrap are eligible."
            ]}

def add_exit(p,session,key,now):
    q=get_quote(session,p["mint"],USDC,p["entry_token_raw"],key)
    usdc=q.get("out_raw") if q["status"]=="QUOTE_AVAILABLE" else None
    change=(usdc/STAKE_RAW-1)*100 if usdc is not None else None
    event={"quoted_utc":now.isoformat(),"status":q["status"],
           "estimated_usdc_out":round(usdc/1e6,6) if usdc is not None else None,
           "estimated_roundtrip_pct":round(change,4) if change is not None else None,
           "router":q.get("router"),"fee_bps":q.get("fee_bps"),
           "http_status":q.get("http_status")}
    p["exit_quotes"].append(event)
    return q["status"]

def advance(state,m1,session,now,key="",sleep_fn=time.sleep):
    validate(state)
    if m1.get("mode")!="M1_MINUTE_RESEARCH_ONLY" or not isinstance(m1.get("candidates"),dict):
        raise ValueError("Invalid M1 source")
    boot=dt(state["started_utc"])
    requests_used=0
    new_entries=0
    # Requests are paced to respect 0.5 RPS keyless tier.
    def quote(input_mint,output_mint,raw):
        nonlocal requests_used
        if requests_used>=MAX_REQUESTS_PER_CYCLE:return None
        if requests_used:sleep_fn(REQUEST_SPACING_SECONDS)
        requests_used+=1
        return get_quote(session,input_mint,output_mint,raw,key)
    # Finish a quote immediately when an entry was recorded. Existing
    # candidates first to avoid starving exit coverage.
    for ident,p in sorted(state["positions"].items()):
        if requests_used>=MAX_REQUESTS_PER_CYCLE:break
        if p.get("entry_token_raw") is None:continue
        age=(now-dt(p["entry_quote_utc"])).total_seconds()
        if age<0 or age>HORIZON_SECONDS:continue
        last=p.get("exit_quotes") or []
        if last and dt(last[-1]["quoted_utc"]) is not None and (now-dt(last[-1]["quoted_utc"])).total_seconds()<50:
            continue
        q=quote(p["mint"],USDC,p["entry_token_raw"])
        if q is None:break
        usdc=q.get("out_raw") if q["status"]=="QUOTE_AVAILABLE" else None
        pct=(usdc/STAKE_RAW-1)*100 if usdc is not None else None
        p["exit_quotes"].append({"quoted_utc":now.isoformat(),
            "status":q["status"],"estimated_usdc_out":round(usdc/1e6,6) if usdc is not None else None,
            "estimated_roundtrip_pct":round(pct,4) if pct is not None else None,
            "router":q.get("router"),"http_status":q.get("http_status")})
    for ident,item in sorted(m1["candidates"].items(),key=lambda kv:kv[1].get("signal_utc","")):
        if ident in state["positions"] or ident in state["skipped"]:continue
        if not isinstance(item,dict) or item.get("research_only") is not True:continue
        sig=dt(item.get("signal_utc")); reg=dt(item.get("registered_utc"))
        if sig is None or reg is None:continue
        if reg<=boot or sig<=boot:
            state["skipped"][ident]="BEFORE_QUOTE_PILOT_BOOTSTRAP"
            continue
        if (now-sig).total_seconds()>MAX_ENTRY_LAG_SECONDS:
            state["skipped"][ident]="STALE_SIGNAL_NO_RETROACTIVE_QUOTE"
            continue
        if (now-sig).total_seconds()<0:continue
        if item.get("mint")==USDC or not isinstance(item.get("mint"),str):
            state["skipped"][ident]="INVALID_MINT"
            continue
        if count_active(state,now)>=MAX_ACTIVE:break
        if requests_used+2>MAX_REQUESTS_PER_CYCLE:break
        buy=quote(USDC,item["mint"],STAKE_RAW)
        if buy is None:break
        p={"mint":item["mint"],"pair":item.get("pair"),"signal_utc":item["signal_utc"],
           "m1_registered_utc":item["registered_utc"],
           "entry_quote_utc":now.isoformat(),"entry_token_raw":buy.get("out_raw"),
           "entry_status":buy["status"],"entry_router":buy.get("router"),
           "entry_usdc_raw":STAKE_RAW,"exit_quotes":[],"research_only":True}
        state["positions"][ident]=p
        new_entries+=1
        if p["entry_token_raw"] is None:
            state["skipped"][ident]="NO_ENTRY_QUOTE"
            continue
        sell=quote(item["mint"],USDC,p["entry_token_raw"])
        if sell is not None:
            usdc=sell.get("out_raw") if sell["status"]=="QUOTE_AVAILABLE" else None
            pct=(usdc/STAKE_RAW-1)*100 if usdc is not None else None
            p["exit_quotes"].append({"quoted_utc":now.isoformat(),
               "status":sell["status"],"estimated_usdc_out":round(usdc/1e6,6) if usdc is not None else None,
               "estimated_roundtrip_pct":round(pct,4) if pct is not None else None,
               "router":sell.get("router"),"http_status":sell.get("http_status")})
    state["last_checked_utc"]=now.isoformat()
    return {"requests_used":requests_used,"new_quote_candidates":new_entries,**summary(state)}

def execute(m1_path,state_path,session,now=None,key=""):
    now=now or datetime.now(timezone.utc)
    target=Path(state_path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.with_name(target.name+".lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        m1=json.loads(Path(m1_path).read_text())
        if not target.exists():
            state=initialize(now,m1)
            with target.open("x") as f:json.dump(state,f,indent=2)
            return {"bootstrapped":True,"old_m1_candidates_excluded":len(m1["candidates"])}
        state=json.loads(target.read_text())
        result=advance(state,m1,session,now,key)
        tmp=target.with_name(target.name+".tmp")
        tmp.write_text(json.dumps(state,indent=2)+"\n")
        tmp.replace(target)
        return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--m1",default=M1)
    parser.add_argument("--state",default=STATE)
    parser.add_argument("--report-only",action="store_true")
    args=parser.parse_args()
    if args.report_only:
        print(json.dumps(summary(json.loads(Path(args.state).read_text())),indent=2))
        return
    with requests.Session() as session:
        print(json.dumps(execute(args.m1,args.state,session,key=os.getenv("JUPITER_API_KEY","").strip())))

if __name__=="__main__":main()
