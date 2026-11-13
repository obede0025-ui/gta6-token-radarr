"""Read-only quality audit of the isolated GTA6 minute research pipeline.

Never calls an API, starts a timer, or writes state. Counts missing liquidity
as UNKNOWN rather than zero, distinguishes repeated momentum observations from
unique tokens, and surfaces quote failure statuses suppressed by systemd's
optional ExecStartPost. This is observability, not an entry-rule change.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_jupiter_minute_quotes import dt

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
MARKET=ROOT/"minute_market_pilot.sqlite"
B3=ROOT/"b3_minute_safety_shadow_state.json"
M1=ROOT/"m1_minute_research_state.json"
JUPITER=ROOT/"jupiter_minute_quotes_state.json"

def distribution(values):
    numbers=sorted(values)
    if not numbers:return {"count":0,"median":None,"p95":None,"max":None}
    n=len(numbers)
    def quantile(p):
        idx=(n-1)*p
        lower=int(idx);upper=min(lower+1,n-1)
        return round(numbers[lower]+(numbers[upper]-numbers[lower])*(idx-lower),3)
    return {"count":n,"median":quantile(.5),"p95":quantile(.95),"max":round(numbers[-1],3)}

def market_audit(db):
    latest=db.execute("SELECT MAX(observed_at) FROM minute_observations").fetchone()[0]
    current=dict(db.execute(
        "SELECT status,COUNT(*) FROM minute_observations WHERE observed_at=? GROUP BY status",
        (latest,)).fetchall()) if latest else {}
    totals=dict(db.execute("SELECT status,COUNT(*) FROM minute_observations GROUP BY status").fetchall())
    # A missing liquidity value may coexist with valid volume/buy/sell.
    partial=db.execute("""
      SELECT COUNT(*) FROM minute_observations
      WHERE status='LIQUIDITY_UNKNOWN' AND price_usd>0
        AND volume_m5 IS NOT NULL AND buys_m5 IS NOT NULL
        AND sells_m5 IS NOT NULL
    """).fetchone()[0]
    latest_missing= db.execute("""
      SELECT COUNT(*) FROM minute_observations
      WHERE observed_at=? AND status='LIQUIDITY_UNKNOWN'
    """,(latest,)).fetchone()[0] if latest else 0
    distinct_last=db.execute("SELECT COUNT(*) FROM minute_observations WHERE observed_at=?",(latest,)).fetchone()[0] if latest else 0
    return {
        "last_market_cycle_utc":latest,"last_cycle_count":distinct_last,
        "last_cycle_statuses":current,"historical_status_counts":totals,
        "latest_liquidity_unknown_pct":round(100*latest_missing/distinct_last,2) if distinct_last else None,
        "liquidity_unknown_with_other_market_fields":partial,
        "interpretation":"Missing Dexscreener pair liquidity is not proven zero; token is not approved."
    }

def signal_audit(b3):
    events=b3.get("events") or []
    matches=[e for e in events if isinstance(e,dict) and
             e.get("momentum_research",{}).get("status")=="MOMENTUM_MATCH_RESEARCH_ONLY"]
    pairs={(e.get("mint"),e.get("pair")) for e in matches}
    reason_counts=Counter(reason for e in events if isinstance(e,dict)
                          for reason in e.get("reasons",[]))
    return {
        "retained_event_count":len(events),
        "retained_momentum_matches":len(matches),
        "unique_matching_pairs_in_retained_events":len(pairs),
        "repeat_momentum_observations":len(matches)-len(pairs),
        "cumulative_first_research_candidates":len(b3.get("research_candidates",[])),
        "retained_rejection_reasons":dict(reason_counts.most_common(12)),
        "note":"Retained events are capped; cumulative candidates may include older events."
    }

def quote_audit(jupiter,m1):
    positions=jupiter.get("positions") or {}
    status=Counter()
    buy_status=Counter()
    timed_entry=[]
    timed_exit=[]
    delayed=[]
    all_exits=0
    for p in positions.values():
        buy_status[p.get("entry_status","UNKNOWN")]+=1
        x=p.get("entry_response_latency_ms")
        if isinstance(x,(float,int)) and not isinstance(x,bool) and 0<=x<60000:timed_entry.append(x)
        entry=dt(p.get("entry_quote_utc"))
        for q in p.get("exit_quotes",[]):
            if not isinstance(q,dict):continue
            all_exits+=1
            status[q.get("status","UNKNOWN")]+=1
            x=q.get("response_latency_ms")
            if isinstance(x,(float,int)) and not isinstance(x,bool) and 0<=x<60000:timed_exit.append(x)
            qtime=dt(q.get("quoted_utc"))
            if entry and qtime and (qtime-entry).total_seconds()>=900:
                delayed.append(q.get("status","UNKNOWN"))
    unknown_skips=Counter(str(x) for x in (jupiter.get("skipped") or {}).values())
    return {
        "positions":len(positions),
        "m1_candidates":len(m1.get("candidates") or {}),
        "entry_status_counts":dict(buy_status),
        "exit_quote_observations":all_exits,
        "exit_status_counts":dict(status),
        "skipped_by_reason":dict(unknown_skips),
        "measured_entry_http_latency_ms":distribution(timed_entry),
        "measured_exit_http_latency_ms":distribution(timed_exit),
        "post_horizon_quote_statuses":dict(Counter(delayed)),
        "note":"HTTP quotes are not fills; missing or errored quotes cannot establish sellability."
    }

def audit(market_db,b3,m1,jupiter,now=None):
    now=now or datetime.now(timezone.utc)
    with sqlite3.connect(Path(market_db).resolve().as_uri()+"?mode=ro",uri=True) as db:
        market=market_audit(db)
    signals=signal_audit(b3)
    quotes=quote_audit(jupiter,m1)
    checks=[]
    t=dt(market["last_market_cycle_utc"]) if market["last_market_cycle_utc"] else None
    lag=(now-t).total_seconds() if t else None
    checks.append({"name":"minute_market_freshness",
                   "status":"PASS" if lag is not None and -30<=lag<=180 else "WARN",
                   "detail":{"age_seconds":round(lag,1) if lag is not None else None}})
    checks.append({"name":"liquidity_data_coverage",
                   "status":"WARN" if market["latest_liquidity_unknown_pct"] is None or
                   market["latest_liquidity_unknown_pct"]>=50 else "PASS",
                   "detail":{"unknown_pct":market["latest_liquidity_unknown_pct"]}})
    observed_failures={k:v for k,v in quotes["exit_status_counts"].items() if k!="QUOTE_AVAILABLE"}
    checks.append({"name":"jupiter_exit_quote_errors",
                   "status":"WARN" if observed_failures else "PASS",
                   "detail":observed_failures})
    checks.append({"name":"jupiter_new_http_timing",
                   "status":"WAITING_FOR_NEW_ENTRY" if not quotes["measured_entry_http_latency_ms"]["count"] else "PASS",
                   "detail":{"samples":quotes["measured_entry_http_latency_ms"]["count"]}})
    return {"mode":"GTA6_READ_ONLY_PIPELINE_QUALITY_AUDIT",
            "checked_utc":now.isoformat(),"market":market,"signals":signals,
            "quotes":quotes,"checks":checks,
            "limitations":[
                "Only tracked 30-token cohort is measured, not the entire Solana launch market.",
                "Repeated momentum matches are observations, not independent entry opportunities.",
                "Missing liquidity remains blocked; no safety threshold is changed.",
                "Jupiter exit failures include historical failures; separate these from recent errors when investigating.",
                "No execution fills or realized profits are inferred.",
                "All files and SQLite are read-only."
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--market",default=str(MARKET))
    p.add_argument("--b3",default=str(B3))
    p.add_argument("--m1",default=str(M1))
    p.add_argument("--jupiter",default=str(JUPITER))
    a=p.parse_args()
    b3=json.loads(Path(a.b3).read_text())
    m1=json.loads(Path(a.m1).read_text())
    jupiter=json.loads(Path(a.jupiter).read_text())
    print(json.dumps(audit(a.market,b3,m1,jupiter),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
