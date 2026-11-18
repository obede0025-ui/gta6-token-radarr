"""V3.3 prospective quote-cohort observer (separate JSON ledger; no trading).

Only new, contemporary first signals are recorded. 5m / 15m follow-ups
request fresh sell quotes for the original hypothetical buy output.
Unavailable/missed exits never become synthetic profits or closed trades.
DB and existing portfolios opened read-only. Writes ONLY the dedicated
research ledger and its own JSON report under data/v33.
"""
import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

from research_preentry_probe_v31 import (
    selection, probe_candidate, KeylessQuoteOnly, USDC, DEFAULT_RPC
)
from research_preentry_evidence_v31 import seconds_since

HORIZONS=(300,900)
LATE_GRACE=120
MAX_NEW=3
MAX_FOLLOWUPS_PER_RUN=6
MAX_MARK_AGE_SEC=120
LEDGER_VERSION=1
STAKE_RAW=10_000_000

def instant(value):
    dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    if dt.tzinfo is None:raise ValueError("naive timestamp")
    return dt.astimezone(timezone.utc)

def new_state(now):
    return {"version":LEDGER_VERSION,"started_utc":now.isoformat(timespec="seconds"),
            "signals":{}, "paper_orders_executed":0, "live_orders_executed":0}

def load_state(path,now):
    if not path.exists():return new_state(now)
    state=json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state,dict) or state.get("version")!=LEDGER_VERSION
        or not isinstance(state.get("signals"),dict)
        or state.get("paper_orders_executed")!=0
        or state.get("live_orders_executed")!=0):
        raise ValueError("Invalid V3.3 ledger; refusing to replace")
    for item in state["signals"].values():
        if not isinstance(item,dict) or not isinstance(item.get("followups"),dict):
            raise ValueError("Corrupt signal ledger")
    return state

def due_followups(signals,now):
    """Return (key,horizon) sorted by deadline; never recover past quotes."""
    ready=[]
    for key,item in signals.items():
        if item.get("buy_raw_out") is None:continue
        start=instant(item["first_seen_utc"])
        age=(now-start).total_seconds()
        for horizon in HORIZONS:
            if str(horizon) not in item["followups"] and age>=horizon:
                ready.append((key,horizon,start))
    return sorted(ready,key=lambda row:(row[2],row[1]))[:MAX_FOLLOWUPS_PER_RUN]

def add_new(state,candidates,now,quoter,rpc_session,rpc_url):
    added=[]
    for c in candidates[:MAX_NEW]:
        key=c["mint"]+"|"+c["pair"]
        if key in state["signals"]:continue
        stamp=now.isoformat(timespec="seconds")
        age=seconds_since(now,c.get("scanned_at_utc"))
        if age is None or age<0 or age>MAX_MARK_AGE_SEC:
            continue
        verdict,route,security=probe_candidate(c,quoter,rpc_session,rpc_url,
                                                 lambda:datetime.now(timezone.utc))
        # V3.3 is an observational cohort. Do not call an entry a fill.
        # The quote must include the hypothetical token amount for later
        # immediate-reversal / horizon sell-route comparability.
        buy_raw=route.get("buy_raw_out")
        item={"mint":c["mint"],"pair":c["pair"],"strategy":c["strategy"],
            "source_scanned_at_utc":c["scanned_at_utc"],
            "first_seen_utc":stamp,"evidence_state":verdict["evidence_state"],
            "entry_rejection_reasons":verdict["reasons"],
            "buy_quote_status":route["buy_status"],
            "sell_reversal_status":route["sell_status"],
            "buy_raw_out":str(buy_raw) if isinstance(buy_raw,int) and buy_raw>0 else None,
            "immediate_reversal_usdc":route.get("immediate_sell_usdc"),
            "security_status":security["security_gate"],
            "followups":{},"fills_verified":0,"live_trade_approval":"BLOCK",
            "paper_trade_approval":"BLOCK"}
        state["signals"][key]=item
        added.append(key)
    return added

def resolve_followups(state,now,quoter):
    outcomes=[]
    for key,horizon,start in due_followups(state["signals"],now):
        item=state["signals"][key]
        observed=now
        elapsed=(observed-start).total_seconds()
        name=str(horizon)
        if elapsed>horizon+LATE_GRACE:
            status="HORIZON_MISSED";out=None
        else:
            try:
                value=quoter.quote(item["mint"],USDC,int(item["buy_raw_out"]))
                out=str(Decimal(value)/Decimal(1_000_000))
                status="JUPITER_SELL_ROUTE_QUOTED"
            except (requests.RequestException,ValueError,TypeError,OverflowError):
                status="SELL_ROUTE_UNAVAILABLE";out=None
        ret=(str((Decimal(out)/Decimal(10)-1)*100)
             if status=="JUPITER_SELL_ROUTE_QUOTED" else None)
        item["followups"][name]={"status":status,"observed_utc":observed.isoformat(timespec="seconds"),
            "elapsed_seconds":round(elapsed,2),"sell_usdc_quote":out,
            "quote_return_pct_before_network_fees":ret}
        outcomes.append((key,name,status))
    return outcomes

def persist(state,ledger,report_path,report):
    # Unique per-run temporary files prevent abandoned *.new collisions.
    # Originals, existing paper portfolios and source DB are untouched.
    for path,obj in ((ledger,state),(report_path,report)):
        path.parent.mkdir(parents=True,exist_ok=True)
        temp_name=None
        try:
            with tempfile.NamedTemporaryFile(mode="w",dir=path.parent,
                    prefix=path.name+".tmp-",delete=False,encoding="utf-8") as file:
                temp_name=file.name
                json.dump(obj,file,indent=2,ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name,path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

def run(db,state_path,ledger,report_path,now=None,rpc_url=DEFAULT_RPC):
    now=now or datetime.now(timezone.utc)
    db,state_path,ledger,report_path=map(Path,(db,state_path,ledger,report_path))
    if len({p.resolve() for p in (db,state_path,ledger,report_path)})!=4:
        raise ValueError("DB, original portfolio, new ledger, and report paths must differ")
    if not rpc_url.startswith("https://"):raise ValueError("HTTPS RPC required")
    state=load_state(ledger,now)
    # No historical replay on bootstrap; all new candidates must be fresh.
    candidates=selection(db,state_path,now,MAX_NEW,ignore_paper_capacity=True)
    with requests.Session() as rpc:
        q=KeylessQuoteOnly()
        try:
            new=add_new(state,candidates,now,q,rpc,rpc_url)
            # Newly recorded signals cannot have horizon quotes yet.
            outcomes=resolve_followups(state,now,q)
        finally:q.close()
    stats={"observing_signals":len(state["signals"]),
           "entry_quote_unavailable":sum(x["buy_raw_out"] is None for x in state["signals"].values()),
           "security_blocked":sum(x["security_status"]!="BASIC_CHECKS_CLEAR" for x in state["signals"].values()),
           "research_evidence_passed":sum(x["evidence_state"]=="PASS_RESEARCH_ONLY" for x in state["signals"].values()),
           "horizon_sell_quotes":sum(y["status"]=="JUPITER_SELL_ROUTE_QUOTED"
               for x in state["signals"].values() for y in x["followups"].values()),
           "horizon_missed":sum(y["status"]=="HORIZON_MISSED"
               for x in state["signals"].values() for y in x["followups"].values()),
           "horizon_sell_unavailable":sum(y["status"]=="SELL_ROUTE_UNAVAILABLE"
               for x in state["signals"].values() for y in x["followups"].values())}
    report={"mode":"V33_QUOTE_COHORT_NO_TRADING","checked_utc":now.isoformat(timespec="seconds"),
            "new_signal_keys":new,"new_count":len(new),
            "followup_outcomes":outcomes,"stats":stats,
            "paper_orders_executed":0,"live_orders_executed":0,
            "source_db_writes":0,"existing_portfolio_writes":0,
            "limitations":["Quote outcomes are not executed fills or realized P/L",
                           "Fees, failures and slippage may differ from quote-only returns",
                           "Missing and missed quotes are not imputed as wins",
                           "No automated orders or authorization from this study"]}
    persist(state,ledger,report_path,report)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/learning.db")
    p.add_argument("--portfolio",default="data/v591/portfolio.json")
    p.add_argument("--ledger",default="data/v33/research_ledger.json")
    p.add_argument("--report",default="data/v33/research_report.json")
    p.add_argument("--rpc-url",default=DEFAULT_RPC)
    a=p.parse_args()
    print(json.dumps(run(a.db,a.portfolio,a.ledger,a.report,rpc_url=a.rpc_url),
        indent=2,ensure_ascii=False))

if __name__=="__main__":main()
