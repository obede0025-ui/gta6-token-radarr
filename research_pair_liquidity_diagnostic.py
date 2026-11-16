"""Read-only diagnosis of repeated liquidity UNKNOWN on selected exact Solana pairs.

One bounded DexScreener GET for up to 20 distinct token mints. No writes to
learning.db, minute_market_pilot.sqlite, states, timers, or live filters.
Alternative pairs are *evidence*, never substituted into original B3/M1.
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from research_minute_market_pilot import API, PILOT, finite

def recent_unknown(db, now, hours=6, limit=20):
    cutoff=(now-timedelta(hours=hours)).isoformat(timespec="seconds")
    return db.execute("""
        SELECT mint,pair,COUNT(*) AS unknown_count,
               MAX(observed_at) AS last_observed,
               MAX(source_snapshot_at) AS last_source_snapshot
        FROM minute_observations
        WHERE observed_at>=? AND status='LIQUIDITY_UNKNOWN'
        GROUP BY mint,pair
        ORDER BY unknown_count DESC, last_observed DESC
        LIMIT ?
    """,(cutoff,limit)).fetchall()

def classify(mint,pinned,payload):
    """Diagnose API availability for an EXACT pair; do not infer execution safety."""
    matches=[p for p in payload
             if isinstance(p,dict) and p.get("chainId")=="solana"
             and (p.get("baseToken") or {}).get("address")==mint]
    exact=[p for p in matches if p.get("pairAddress")==pinned]
    alternatives=[p for p in matches if p.get("pairAddress")!=pinned]
    good_alternatives=[]
    for p in alternatives:
        price=finite(p.get("priceUsd"))
        liquidity=finite((p.get("liquidity") or {}).get("usd"))
        if price is not None and price>0 and liquidity is not None and liquidity>=0:
            good_alternatives.append({
                "pair":p.get("pairAddress"),"price_usd":price,
                "liquidity_usd":liquidity})
    good_alternatives.sort(key=lambda p:p["liquidity_usd"],reverse=True)
    if not matches:
        status="TOKEN_NOT_RETURNED"
    elif not exact:
        status="PINNED_PAIR_NOT_RETURNED"
    else:
        price=finite(exact[0].get("priceUsd"))
        liquidity=finite((exact[0].get("liquidity") or {}).get("usd"))
        if price is None or price<=0:
            status="EXACT_PRICE_NOT_AVAILABLE"
        elif liquidity is None:
            status="EXACT_LIQUIDITY_NOT_REPORTED"
        elif liquidity<0:
            status="EXACT_INVALID_NEGATIVE_LIQUIDITY"
        else:
            status="EXACT_LIQUIDITY_NOW_REPORTED"
    return {"classification":status,
            "exact_pair_returned":bool(exact),
            "other_pairs_returned":len(alternatives),
            "alternatives_with_known_liquidity":len(good_alternatives),
            "top_alternative_research_only":good_alternatives[0] if good_alternatives else None}

def audit(db_path=PILOT, now=None, hours=6, limit=20, session=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or hours<=0 or not 1<=limit<=20:
        raise ValueError("UTC-aware timestamp, positive hours, limit 1..20 required")
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows=recent_unknown(db,now,hours,limit)
    mints=list(dict.fromkeys(row[0] for row in rows))
    if session is None:
        with requests.Session() as client:
            payload=fetch_payload(client,mints)
    else:
        payload=fetch_payload(session,mints)
    detailed=[]
    for mint,pair,count,last_observed,source_snapshot in rows:
        result=classify(mint,pair,payload)
        result.update({"mint":mint,"pinned_pair":pair,
                       "unknown_observations_in_window":count,
                       "last_market_observation_utc":last_observed,
                       "original_collector_snapshot_utc":source_snapshot})
        detailed.append(result)
    from collections import Counter
    return {"mode":"READ_ONLY_PINNED_PAIR_LIQUIDITY_DIAGNOSTIC",
            "fetched_utc":now.isoformat(),
            "hours":hours,"inspected_pairs":len(rows),
            "requested_unique_mints":len(mints),
            "classifications":dict(Counter(x["classification"] for x in detailed)),
            "with_viable_alternative_pair":sum(x["alternatives_with_known_liquidity"]>0 for x in detailed),
            "results":detailed,
            "limitations":[
                "One contemporaneous API snapshot, not a historical reconstruction.",
                "source_snapshot_at is the original learning.db scanned_at, NOT DexScreener API response time.",
                "A different pair may be a different market or quote asset and is never silently substituted.",
                "Price and liquidity do not prove swap route availability, safe exits or profitability.",
                "No production state, filter, timer or original database is modified."]}

def fetch_payload(session,mints):
    if not mints:
        return []
    response=session.get(API+(",".join(mints)),timeout=20)
    response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,list):
        raise ValueError("Unexpected DexScreener response shape")
    return payload

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default=PILOT)
    p.add_argument("--hours",type=float,default=6)
    p.add_argument("--limit",type=int,default=20)
    args=p.parse_args()
    print(json.dumps(audit(args.db,hours=args.hours,limit=args.limit),indent=2))

if __name__=="__main__":
    main()
