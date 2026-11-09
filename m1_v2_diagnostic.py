"""M1 V2 consolidated retrospective diagnostics. Read-only, not a trading strategy."""
import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import timedelta, datetime, timezone
from pathlib import Path

from b3_candidate_audit import parsed
from m1_micro_15m_research import evaluate as m1_evaluate

def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None

def feature_history(conn, event):
    timestamp = parsed(event["scanned_at"])
    begin = timestamp - timedelta(minutes=15)
    observations = []
    # SQLite stored timestamps have mixed ISO formats (Z and +00:00).
    # Never rely on lexical ordering to compare different suffixes.
    for row in conn.execute(
        """SELECT scanned_at,buys_m5,sells_m5,volume_m5,liquidity_usd
           FROM snapshots WHERE token_address=? AND pair_address=?
           ORDER BY scanned_at""", (event["mint"], event["pair"])
    ):
        t = parsed(row[0])
        if begin <= t <= timestamp:
            observations.append((t, *row[1:]))
    observations.sort(key=lambda r:r[0])
    if len(observations) < 2:
        return {"status":"INSUFFICIENT_HISTORY","samples":len(observations)}
    first, last = observations[0], observations[-1]
    b0,s0,v0,l0 = map(finite,first[1:])
    b1,s1,v1,l1 = map(finite,last[1:])
    if any(x is None for x in (b0,s0,v0,b1,s1,v1)) or v0 <= 0:
        return {"status":"INVALID_HISTORY","samples":len(observations)}
    surplus0=b0-s0
    surplus1=b1-s1
    volume_change=(v1/v0-1)*100
    surplus_change=(surplus1/surplus0-1)*100 if surplus0>0 else None
    liquidity_change=(l1/l0-1)*100 if l0 and l1 is not None else None
    exhaustion=bool(volume_change<=-60 and surplus_change is not None and surplus_change<=-50)
    return {"status":"MEASURED","samples":len(observations),
            "volume_change_pct":round(volume_change,3),
            "buy_surplus_before":round(surplus0,2),
            "buy_surplus_at_signal":round(surplus1,2),
            "buy_surplus_change_pct":round(surplus_change,3) if surplus_change is not None else None,
            "liquidity_change_pct":round(liquidity_change,3) if liquidity_change is not None else None,
            "exhaustion_research_flag":exhaustion}

def summarize(records):
    result={}
    for group in ("MICRO", "OTHER"):
        items=[x for x in records if x["group"]==group]
        observed=[x for x in items if x["observed_change_pct"] is not None]
        returns=[x["observed_change_pct"] for x in observed]
        result[group]={
            "tokens":len(items),
            "outcomes_observed":len(observed),
            "outcomes_missing":len(items)-len(observed),
            "median_price_change_pct":round(statistics.median(returns),3) if returns else None,
            "mean_price_change_pct":round(statistics.mean(returns),3) if returns else None,
            "observed_price_gain_count":sum(x>0 for x in returns),
            "observed_large_loss_count":sum(x<=-50 for x in returns),
            "exit_labels":dict(Counter(x["exit_reason"] or x["outcome_status"] for x in items)),
            "pre_signal_exhaustion_flagged":sum(x.get("exhaustion_research_flag") is True for x in items),
            "history_missing":sum(x["history_status"]!="MEASURED" for x in items),
        }
    return result

def report(db, shadow, now=None):
    base=m1_evaluate(db,shadow,now=now)
    state=json.loads(Path(shadow).read_text(encoding="utf-8"))
    first={}
    for event in sorted(state["events"],key=lambda e:e.get("scanned_at","")):
        if event.get("decision")=="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW":
            first.setdefault(event["mint"],event)
    if not Path(db).is_file():
        raise FileNotFoundError("Market DB missing")
    lines=[]
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        for outcome in base["outcomes"]:
            event=first.get(outcome["mint"])
            if event is None:continue
            features=feature_history(conn,event)
            lines.append({
                "mint":outcome["mint"],
                "signal_utc":event["scanned_at"],
                "group":outcome["group"],
                "history_status":features["status"],
                "history_samples":features["samples"],
                "volume_change_pct":features.get("volume_change_pct"),
                "buy_surplus_change_pct":features.get("buy_surplus_change_pct"),
                "liquidity_change_pct":features.get("liquidity_change_pct"),
                "exhaustion_research_flag":features.get("exhaustion_research_flag"),
                "outcome_status":outcome["status"],
                "exit_reason":outcome["exit_reason"],
                "observed_change_pct":outcome["observed_change_pct"],
                "exit_liquidity_usd":outcome.get("liquidity_usd")
            })
    return {"mode":"M1_V2_CONSOLIDATED_READ_ONLY",
            "built_utc":(now or datetime.now(timezone.utc)).isoformat(),
            "summary":summarize(lines),"candidates":lines,
            "notes":[
                "Historically fitted research flags; cannot claim prediction without a fresh cohort.",
                "Buy/sell counts are not buy/sell amounts.",
                "Observed market-price TP/SL signals are not executable orders or realized PnL.",
                "No Jupiter quotes, spreads, fees, price impact, security audit or wallet.",
                "The 15 minute deadline is a research hypothesis; sampled exits may occur up to 3 minutes after it.",
                "Existing paper portfolios and shadow states are read-only."
            ]}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",required=True)
    parser.add_argument("--shadow",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    result=report(args.db,args.shadow)
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+".tmp")
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temporary.replace(target)
    print(json.dumps(result["summary"],ensure_ascii=False,indent=2))
if __name__=="__main__":
    main()
