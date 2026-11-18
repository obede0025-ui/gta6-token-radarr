"""GTA6 risk gate V2.9: offline, fail-closed assessment of existing reports.

Never approves or executes a trade. It identifies stale/illiquid paper
positions, unsupported sell exits, missing security evidence and rate limits.
Inputs and databases are READ ONLY; output is JSON stdout only.
"""
import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MAX_MARK_MIN=12
MIN_LIQUIDITY=20000.0
MAX_POSITION_AGE_HOURS=4.0
MAX_REPORT_AGE_MIN=15
TRANSIENT={"HTTP_429","HTTP_500","HTTP_502","HTTP_503","HTTP_504","TIMEOUT","NETWORK_ERROR"}

def utc(raw):
    if not isinstance(raw,str) or not raw:
        return None
    try:
        t=datetime.fromisoformat(raw.replace("Z","+00:00"))
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    except (ValueError,OverflowError):return None

def number(v):
    try:
        if isinstance(v,bool):return None
        val=float(v)
        return val if math.isfinite(val) else None
    except (TypeError,ValueError,OverflowError):return None

def read(path):
    try:
        data=json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data,dict):return {}, "INVALID_JSON_ROOT"
        return data,None
    except FileNotFoundError:return {}, "MISSING_REPORT"
    except (OSError,ValueError):return {}, "INVALID_REPORT"

def old(now,stamp,max_min):
    t=utc(stamp)
    return t is None or (now-t).total_seconds()<0 or (now-t).total_seconds()>max_min*60

def quote_map(quotes):
    rows=quotes.get("positions")
    if not isinstance(rows,list):return {}
    return {r.get("mint"):r for r in rows if isinstance(r,dict) and isinstance(r.get("mint"),str)}

def execution_map(execution):
    rows=execution.get("positions")
    if not isinstance(rows,list):return {}
    grouped={}
    for r in rows:
        if isinstance(r,dict) and isinstance(r.get("mint"),str):
            grouped.setdefault((r.get("strategy"),r["mint"]),[]).append(r)
    return grouped

def assess(health,quotes,security,execution,now):
    health_errors=[]
    if old(now,health.get("checked_at_utc"),MAX_REPORT_AGE_MIN):health_errors.append("HEALTH_REPORT_STALE_OR_MISSING")
    if old(now,quotes.get("checked_utc"),MAX_REPORT_AGE_MIN):health_errors.append("QUOTE_REPORT_STALE_OR_MISSING")
    if old(now,security.get("checked_utc"),MAX_REPORT_AGE_MIN):health_errors.append("SECURITY_REPORT_STALE_OR_MISSING")
    if old(now,execution.get("checked_utc"),MAX_REPORT_AGE_MIN):health_errors.append("EXECUTION_REPORT_STALE_OR_MISSING")
    if health.get("status") not in ("OK","AANDACHT"):health_errors.append("HEALTH_STATUS_INVALID")
    if quotes.get("status") not in ("OK","PARTIAL"):health_errors.append("QUOTE_STATUS_UNAVAILABLE")
    if security.get("all_live_trades_blocked") is not True:health_errors.append("SECURITY_LIVE_BLOCK_NOT_CONFIRMED")
    if execution.get("live_orders_enabled") is not False:health_errors.append("EXECUTION_LIVE_BLOCK_NOT_CONFIRMED")
    per_mint=quote_map(quotes)
    ex_map=execution_map(execution)
    tokens={r.get("mint"):r for r in security.get("tokens",[]) if isinstance(r,dict) and isinstance(r.get("mint"),str)}
    positions=health.get("open_positions") if isinstance(health.get("open_positions"),list) else []
    rows=[];reason_totals=Counter()
    blocked_count=0;rate_limited=set()
    for p in positions:
        if not isinstance(p,dict):continue
        mint=p.get("token")
        strategy=p.get("strategy")
        reasons=list(health_errors)
        age=number(p.get("last_market_age_minutes"))
        liq=number(p.get("market_liquidity_usd"))
        position_age=number(p.get("age_hours"))
        if age is None or age<0 or age>MAX_MARK_MIN:reasons.append("STALE_OR_UNKNOWN_MARK")
        if liq is None or liq<MIN_LIQUIDITY:reasons.append("LOW_OR_UNKNOWN_LIQUIDITY")
        if position_age is None or position_age<0 or position_age>MAX_POSITION_AGE_HOURS:
            reasons.append("POSITION_AGE_EXCEEDS_RESEARCH_HORIZON")
        token_security=tokens.get(mint)
        if not token_security or token_security.get("security_gate")!="BASIC_CHECKS_CLEAR":
            reasons.append("TOKEN_SECURITY_UNVERIFIED")
        quote_item=per_mint.get(mint)
        matching=[x for x in (quote_item or {}).get("positions",[]) if isinstance(x,dict) and x.get("strategy")==strategy]
        if not matching or any(x.get("status")!="QUOTE_AVAILABLE" for x in matching):
            reasons.append("PAPER_EXIT_QUOTE_MISSING")
        for item in matching:
            state=item.get("status")
            if state in TRANSIENT:rate_limited.add(state)
        for warning in (quote_item or {}).get("warnings",[]):
            if isinstance(warning,str) and "HTTP_429" in warning:rate_limited.add("HTTP_429")
        for record in ex_map.get((strategy,mint),[]):
            if record.get("status")!="QUOTE_AVAILABLE":
                reasons.append("EXECUTION_ACCURACY_UNVERIFIED")
            if record.get("reason") in TRANSIENT:rate_limited.add(record["reason"])
        # Missing B2 execution rows do not imply a verified exit for A/C.
        reasons=sorted(set(reasons))
        reason_totals.update(reasons)
        if reasons:blocked_count+=1
        rows.append({
            "strategy":strategy,"mint":mint,"pair":p.get("pair"),
            "mark_age_minutes":age,"liquidity_usd":liq,
            "position_age_hours":position_age,
            "risk_state":"BLOCKED_UNVERIFIED" if reasons else "NO_BLOCKS_FOUND_NOT_APPROVED",
            "paper_entry_permission":"BLOCK",
            "live_trade_permission":"BLOCK",
            "reasons":reasons
        })
    overall=("BLOCKED" if health_errors or blocked_count else
             "UNVERIFIED_NO_APPROVAL")
    return {
        "mode":"GTA6_RISK_GATE_V29_OFFLINE",
        "checked_utc":now.isoformat(timespec="seconds"),
        "status":overall,
        "policy":{"max_market_age_minutes":MAX_MARK_MIN,
                  "min_liquidity_usd":MIN_LIQUIDITY,
                  "max_position_age_hours":MAX_POSITION_AGE_HOURS,
                  "max_report_age_minutes":MAX_REPORT_AGE_MIN},
        "positions_checked":len(rows),
        "positions_blocked":blocked_count,
        "report_risks":health_errors,
        "rate_limit_signals":sorted(rate_limited),
        "reason_counts":dict(reason_totals),
        "positions":rows,
        "recommended_next_steps":[
            "Do not treat stale market marks or missing sell quotes as recoverable value.",
            "Quote HTTP 429 calls should use rate limiting and capped backoff in a separately tested collector revision.",
            "Frozen old paper positions need a dedicated close-policy experiment; never silently overwrite portfolio history.",
            "No live entries until independently verified execution, exitability and token safety."
        ],
        "safety":{"api_requests":0,"database_writes":0,"paper_portfolio_writes":0,
                  "orders":0,"active_services_changed":0},
        "limitations":[
            "Offline report does NOT enforce permissions in running collectors.",
            "Market age and liquidity alone cannot prove that an exit is executable.",
            "No verified trading approval is ever produced by this audit."
        ]
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",default=".")
    args=p.parse_args()
    root=Path(args.root)
    now=datetime.now(timezone.utc)
    health,e1=read(root/"data/v594/radar_healthcheck.json")
    quotes,e2=read(root/"data/v594/jupiter_quote_monitor.json")
    security,e3=read(root/"data/v594/token_security.json")
    execution,e4=read(root/"data/v612/execution_accuracy.json")
    result=assess(health,quotes,security,execution,now)
    result["input_errors"]={k:v for k,v in
      (("health",e1),("quotes",e2),("security",e3),("execution",e4)) if v}
    if result["input_errors"]:result["status"]="BLOCKED"
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__":main()
