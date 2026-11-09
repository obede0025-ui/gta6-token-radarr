"""M1 experimental 15-minute micro-momentum forward-price study.

READ ONLY: no exchange access, wallet, paper portfolio or trade execution.
Entry = first newly observed B3-qualified micro event per token. Exit
= first observed 15% rise, 10% fall, or first observation >=15 minutes,
within 3m grace. Never pretends a 5m snapshot hits an unseen price.
"""
import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from b3_candidate_audit import parsed

TP=0.15
SL=-0.10
HOLD=15
MAX_LAG=3

def positive(value):
    try:
        x=float(value)
        return x if math.isfinite(x) and x>0 else None
    except (TypeError,ValueError,OverflowError):
        return None

def micro_candidate(buys,sells,avg):
    try:
        b,s,a=float(buys),float(sells),float(avg)
        return (all(map(math.isfinite,(b,s,a))) and s>0 and
                b+s>1000 and 0<a<2 and .95<=b/s<=1.15)
    except (TypeError,ValueError,OverflowError,ZeroDivisionError):
        return False

def evaluate(db_path,shadow_path,now=None):
    now=now or datetime.now(timezone.utc)
    shadow=json.loads(Path(shadow_path).read_text(encoding="utf-8"))
    if shadow.get("version")!=1 or not isinstance(shadow.get("events"),list):
        raise ValueError("Invalid B3 state")
    selected={}
    for e in sorted(shadow["events"],key=lambda e:e.get("scanned_at","")):
        if e.get("decision")!="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW":
            continue
        mint,pair=e.get("mint"),e.get("pair")
        if isinstance(mint,str) and mint and isinstance(pair,str) and pair:
            selected.setdefault(mint,e)
    outcomes=[]
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        for mint,e in selected.items():
            stamp=e["scanned_at"]
            row=conn.execute("""SELECT buys_m5,sells_m5,average_trade_usd
               FROM snapshots WHERE token_address=? AND pair_address=?
               AND scanned_at=? LIMIT 1""",(mint,e["pair"],stamp)).fetchone()
            is_micro=bool(row and micro_candidate(*row))
            entry=positive(e.get("price_usd"))
            t0=parsed(stamp)
            record={"mint":mint,"scanned_at":stamp,"group":"MICRO" if is_micro else "OTHER",
                    "status":"UNAVAILABLE","exit_reason":None,"observed_change_pct":None}
            if not entry or t0>now:
                outcomes.append(record);continue
            deadline=t0+timedelta(minutes=HOLD)
            # No future price data beyond now; same pair only.
            rows=conn.execute("""SELECT scanned_at,price_usd,liquidity_usd
               FROM snapshots WHERE token_address=? AND pair_address=?
               AND scanned_at>? AND scanned_at<=?
               ORDER BY scanned_at ASC""",
               (mint,e["pair"],stamp,min(now,deadline+timedelta(minutes=MAX_LAG)).isoformat())
            ).fetchall()
            hit=False
            for tstr,p,liq in rows:
                t=parsed(tstr)
                price=positive(p)
                if price is None:continue
                change=price/entry-1
                if t<=deadline and (change>=TP or change<=SL):
                    record.update(status="OBSERVED_PRICE_TRIGGER",exit_reason="TP" if change>=TP else "SL",
                                  observed_change_pct=round(change*100,4),
                                  observed_at=tstr,liquidity_usd=liq)
                    hit=True;break
                if t>=deadline:
                    record.update(status="OBSERVED_TIME_EXIT",exit_reason="TIME",
                                  observed_change_pct=round(change*100,4),
                                  observed_at=tstr,liquidity_usd=liq)
                    hit=True;break
            if not hit:
                record["status"]="PENDING" if now<deadline+timedelta(minutes=MAX_LAG) else "MISSING_EXIT_SAMPLE"
            outcomes.append(record)
    totals={}
    for label in ("MICRO","OTHER"):
        selection=[x for x in outcomes if x["group"]==label]
        observed=[x["observed_change_pct"] for x in selection if x["observed_change_pct"] is not None]
        totals[label]={"candidates":len(selection),"observed":len(observed),
            "mean_pct":round(sum(observed)/len(observed),4) if observed else None,
            "positive":sum(x>0 for x in observed),
            "missing_or_pending":len(selection)-len(observed)}
    return {"mode":"M1_MICRO_15M_PRICE_STUDY_READ_ONLY","evaluated_utc":now.isoformat(),
            "thresholds":{"micro_min_transactions_5m":1001,"micro_avg_trade_under_usd":2,
                          "buy_sell_ratio_min":.95,"buy_sell_ratio_max":1.15,
                          "take_profit_pct":15,"stop_loss_pct":-10,"timeout_minutes":15},
            "summary":totals,"outcomes":outcomes,
            "warning":"Retrospective pilot on known data, prone to overfitting. Observed prices are NOT executable exits; TP/SL snapshots may skip thresholds, transaction costs omitted. Do not use as real trades."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--shadow",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    report=evaluate(a.db,a.shadow)
    target=Path(a.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".tmp")
    temp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    temp.replace(target)
    print(json.dumps(report["summary"],ensure_ascii=False))
if __name__=="__main__":
    main()
