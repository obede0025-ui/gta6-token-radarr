"""V3.4 offline observer health and metrics. No network, orders or state changes."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

HORIZONS=("300","900")
MAX_AGE_MINUTES=12
MAX_UNRESOLVED_AGE_MINUTES=22
MIN_SAMPLE=100

def utc(value):
    if not isinstance(value,str):return None
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError,OverflowError):return None

def decimal(value):
    if value is None or isinstance(value,bool):return None
    try:
        v=Decimal(str(value))
        return v if v.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def metrics(ledger,report=None,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("Timezone required")
    report=report if isinstance(report,dict) else {}
    problems=[]
    if not isinstance(ledger,dict) or ledger.get("version")!=1 or not isinstance(ledger.get("signals"),dict):
        return {"mode":"V34_OBSERVER_HEALTH_READ_ONLY","status":"ERROR",
            "alerts":["INVALID_OR_MISSING_V33_LEDGER"],"signal_count":0,
            "quote_samples":0,"sample_sufficient":False,"trades_executed":0}
    if ledger.get("paper_orders_executed")!=0 or ledger.get("live_orders_executed")!=0:
        problems.append("UNEXPECTED_TRADING_COUNTER")
    signals=ledger["signals"]
    counts=Counter();returns=[];eligible=0;unresolved=0
    rejections=Counter();security=Counter();per_horizon={h:Counter() for h in HORIZONS}
    for key,item in signals.items():
        if not isinstance(item,dict) or not isinstance(item.get("followups"),dict):
            problems.append("INVALID_SIGNAL_RECORD")
            continue
        if item.get("paper_trade_approval")!="BLOCK" or item.get("live_trade_approval")!="BLOCK":
            problems.append("ENTRY_PERMISSION_NOT_BLOCKED")
        if item.get("evidence_state")=="PASS_RESEARCH_ONLY":eligible+=1
        security[item.get("security_status") or "UNKNOWN"]+=1
        reasons=item.get("entry_rejection_reasons",[])
        if not isinstance(reasons,list):
            problems.append("INVALID_REJECTION_REASONS")
        else:
            for reason in set(x for x in reasons if isinstance(x,str)):
                rejections[reason]+=1
        entry=utc(item.get("first_seen_utc"))
        if entry is None or entry>now:
            problems.append("INVALID_SIGNAL_TIMESTAMP")
            continue
        age=(now-entry).total_seconds()/60
        if age>MAX_UNRESOLVED_AGE_MINUTES and item.get("buy_raw_out") and any(
                h not in item["followups"] for h in HORIZONS):
            unresolved+=1
        for horizon in HORIZONS:
            follow=item["followups"].get(horizon)
            if follow is None:continue
            if not isinstance(follow,dict):
                problems.append("INVALID_FOLLOWUP_RECORD");continue
            status=follow.get("status")
            counts[status or "UNKNOWN"]+=1
            per_horizon[horizon][status or "UNKNOWN"]+=1
            if status=="JUPITER_SELL_ROUTE_QUOTED":
                val=decimal(follow.get("quote_return_pct_before_network_fees"))
                if val is None:
                    problems.append("INVALID_QUOTE_RETURN")
                else:returns.append(val)
    if unresolved:problems.append("FOLLOWUPS_OVERDUE")
    if report:
        stamp=utc(report.get("checked_utc"))
        if stamp is None or (now-stamp).total_seconds()/60>MAX_AGE_MINUTES or stamp>now:
            problems.append("REPORT_STALE_OR_INVALID")
    else:problems.append("REPORT_MISSING")
    total=len(signals)
    num_quote=counts["JUPITER_SELL_ROUTE_QUOTED"]
    # Missing routes remain failures/unknowns; do NOT calculate "success rate"
    # over just the positive-quote sample.
    pct=(str(round(sum(returns)/len(returns),3)) if returns else None)
    results={
        "mode":"V34_OBSERVER_HEALTH_READ_ONLY",
        "checked_utc":now.isoformat(timespec="seconds"),
        "status":"ATTENTION" if problems else "OK",
        "status_meaning":"Observer operational health only; NOT token or trading safety",
        "alerts":sorted(set(problems)),
        "signal_count":total,
        "research_evidence_passed":eligible,
        "security_gate_counts":dict(sorted(security.items())),
        "entry_rejection_reason_counts":dict(sorted(rejections.items())),
        "followups_by_horizon_seconds":{
            h:{"quoted":per_horizon[h]["JUPITER_SELL_ROUTE_QUOTED"],
               "missed":per_horizon[h]["HORIZON_MISSED"],
               "unavailable":per_horizon[h]["SELL_ROUTE_UNAVAILABLE"],
               "pending":sum(1 for item in signals.values()
                   if isinstance(item,dict)
                   and isinstance(item.get("followups"),dict)
                   and item.get("buy_raw_out")
                   and h not in item["followups"])}
            for h in HORIZONS},
        "quote_samples":num_quote,
        "quote_return_average_pct_observed_only":pct,
        "horizon_missed":counts["HORIZON_MISSED"],
        "sell_unavailable":counts["SELL_ROUTE_UNAVAILABLE"],
        "followups_overdue":unresolved,
        "sample_sufficient":total>=MIN_SAMPLE,
        "trades_executed":0,
        "warnings":["Averages exclude failed/missing exits and are not returns on executed trades",
                    "100 signals is a minimum review threshold, not proof of profitability",
                    "Quote-only values exclude fill risk, fees and slippage"]
    }
    return results

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ledger",default="data/v33/research_ledger.json")
    p.add_argument("--report",default="data/v33/research_report.json")
    a=p.parse_args()
    try:
        ledger=json.loads(Path(a.ledger).read_text(encoding="utf-8"))
    except (OSError,ValueError):
        ledger=None
    try:
        report=json.loads(Path(a.report).read_text(encoding="utf-8"))
    except (OSError,ValueError):
        report=None
    print(json.dumps(metrics(ledger,report),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
