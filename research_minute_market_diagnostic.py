"""One-shot diagnosis of unavailable minute-market observations and B3 spacing.

Reads the isolated minute SQLite database. Optionally makes ONE public Dexscreener
batch request for at most 30 mints. Never writes DB/state, places orders, or
interprets a different pair as an approved substitute.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_DB="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
API="https://api.dexscreener.com/tokens/v1/solana/"

def latest(db):
    stamp=db.execute("SELECT MAX(observed_at) FROM minute_observations").fetchone()[0]
    if not stamp:
        return None,[]
    rows=db.execute("""
        SELECT mint,pair,status,price_usd,liquidity_usd
        FROM minute_observations WHERE observed_at=? ORDER BY mint
    """,(stamp,)).fetchall()
    return stamp,rows

def market_classification(wanted,payload):
    """Classify pinned pair. Alternate pairs never replace original pair."""
    bymint=defaultdict(list)
    for p in payload:
        if not isinstance(p,dict) or p.get("chainId")!="solana":continue
        mint=(p.get("baseToken") or {}).get("address")
        if mint in wanted:bymint[mint].append(p)
    classes={}
    for mint,pair in wanted.items():
        pairs=bymint.get(mint,[])
        exact=next((p for p in pairs if p.get("pairAddress")==pair),None)
        if exact is None:
            classes[mint]="ALTERNATE_PAIR_ONLY" if pairs else "NO_SOLANA_PAIR_RETURNED"
            continue
        try:
            price=float(exact.get("priceUsd"))
            liq=float((exact.get("liquidity") or {}).get("usd"))
            import math
            valid=math.isfinite(price) and price>0 and math.isfinite(liq) and liq>=0
        except (TypeError,ValueError,OverflowError):
            valid=False
        classes[mint]="EXACT_PAIR_VALID" if valid else "EXACT_PAIR_INVALID_MARKET"
    return classes

def historical_spacing(db, stamp, candidates):
    # Descriptive only: 1-minute observations do not satisfy the existing
    # B3 recent_pair_history 3.5..20 minute consecutive sample spacing.
    results=Counter()
    for mint,pair in candidates:
        rows=db.execute("""
            SELECT observed_at FROM minute_observations
            WHERE mint=? AND pair=? AND status='QUOTE_MARKET_OBSERVED'
              AND observed_at<=? ORDER BY observed_at DESC LIMIT 5
        """,(mint,pair,stamp)).fetchall()
        if len(rows)<3:
            results["FEWER_THAN_THREE_VALID_SAMPLES"]+=1
            continue
        times=[datetime.fromisoformat(r[0]) for r in rows]
        gaps=[(times[i]-times[i+1]).total_seconds()/60 for i in range(2)]
        if all(3.5<=g<=20 for g in gaps):
            results["LAST_TWO_GAPS_IN_LEGACY_RANGE"]+=1
        else:
            results["LAST_TWO_GAPS_NOT_LEGACY_COMPATIBLE"]+=1
    return dict(results)

def run(dbpath,check_api=True):
    with sqlite3.connect(Path(dbpath).resolve().as_uri()+"?mode=ro",uri=True) as db:
        stamp,rows=latest(db)
        if not stamp:
            return {"status":"NO_MINUTE_OBSERVATIONS"}
        wanted={mint:pair for mint,pair,status,_,_ in rows if status=="MARKET_DATA_UNAVAILABLE"}
        status_counts=dict(Counter(x[2] for x in rows))
        # Importantly inspect *all* chosen tokens for momentum sample coverage.
        spacing=historical_spacing(db,stamp,[(x[0],x[1]) for x in rows])
        recent=db.execute("""
            SELECT observed_at,
                   COUNT(*) total,
                   SUM(status='QUOTE_MARKET_OBSERVED') valid,
                   SUM(status='MARKET_DATA_UNAVAILABLE') missing
            FROM minute_observations
            GROUP BY observed_at ORDER BY observed_at DESC LIMIT 5
        """).fetchall()
    out={"mode":"READ_ONLY_MINUTE_MARKET_DIAGNOSTIC",
         "latest_cycle_utc":stamp, "last_cycle":status_counts,
         "missing_unique_mints_latest_cycle":len(wanted),
         "momentum_history_diagnostic":spacing,
         "recent_cycles":[{"utc":t,"total":n,"valid":v,"missing":m} for t,n,v,m in recent],
         "no_execution_or_state_changes":True}
    if not check_api:
        out["pair_check"]="SKIPPED"
        return out
    if not wanted:
        out["pair_check"]={"checked":0,"categories":{}}
        return out
    # The endpoint accepts 30 token addresses per request; keep exactly one call.
    if len(wanted)>30:
        raise ValueError("Refuse >30 mints in one diagnostic request")
    try:
        with requests.Session() as session:
            r=session.get(API+",".join(sorted(wanted)),timeout=20)
            r.raise_for_status()
            payload=r.json()
        if not isinstance(payload,list):raise ValueError("Unexpected market payload")
        categories=market_classification(wanted,payload)
        out["pair_check"]={"checked":len(wanted),
                           "categories":dict(Counter(categories.values())),
                           "examples":{name:[mint[:12] for mint,cat in categories.items()
                                               if cat==name][:3]
                                       for name in sorted(set(categories.values()))}}
    except (requests.RequestException,ValueError) as e:
        out["pair_check"]={"status":"API_FAILED","error_type":type(e).__name__,
                           "note":"No inference about whether pairs exist"}
    out["limitations"]=[
        "Checks exact original pairs; alternate pairs cannot be used as exits without separate audit.",
        "A public Dexscreener response may be incomplete or temporarily stale.",
        "Legacy B3 momentum needs two earlier samples spaced 3.5-20 minutes; minute samples are not automatically interchangeable.",
        "Existing data are selected from a 30-mint watchlist, not the entire market."
    ]
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=DEFAULT_DB)
    p.add_argument("--no-api",action="store_true")
    args=p.parse_args()
    print(json.dumps(run(args.db,not args.no_api),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
