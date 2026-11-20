"""Research-only classification of minute-price spikes versus market liquidity.

Exact mint/pair, real observation timestamps, no historical Jupiter fabrication.
This module is intentionally independent of portfolio, scanner and trading logic.
"""
import argparse
import json
import math
import sqlite3
from bisect import bisect_left
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_ENTRY_LIQ_USD = 20000
MIN_ENTRY_VOL_5M = 250
MIN_GAIN_PCT = 20
EXTREME_GAIN_PCT = 100
COLLAPSE_DROP_PCT = 80
MAX_HORIZON_LAG_SECONDS = 60

def utc(raw):
    if not isinstance(raw, str):
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, OverflowError):
        return None

def finite(raw):
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        n = float(raw)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None

def classify(entry_price, exit_price, entry_liq, exit_liq, verified_sell_quote=False):
    """Returns separate price/liquidity label and sale-quote evidence label.

    A quote label never substitutes for liquidity-collapse classification.
    """
    p0, p1, l0, l1 = map(finite,(entry_price,exit_price,entry_liq,exit_liq))
    quote = "SELL_QUOTE_VERIFIED_RESEARCH" if verified_sell_quote else "SELL_QUOTE_UNAVAILABLE_OR_UNCHECKED"
    if p0 is None or p0 <= 0 or p1 is None or p1 <= 0 or l0 is None or l0 <= 0 or l1 is None or l1 < 0:
        return "INSUFFICIENT_MARKET_EVIDENCE", quote
    gain = (p1/p0-1)*100
    liquidity_drop = (1-l1/l0)*100
    if l1 < MIN_ENTRY_LIQ_USD or liquidity_drop >= COLLAPSE_DROP_PCT:
        if gain > 0:
            return "MOMENTUM_LIQUIDITY_COLLAPSE", quote
        return "LIQUIDITY_COLLAPSE_WITH_PRICE_LOSS", quote
    if gain >= EXTREME_GAIN_PCT and not verified_sell_quote:
        return "PRICE_SPIKE_UNVERIFIED", quote
    if gain >= MIN_GAIN_PCT and l1 >= MIN_ENTRY_LIQ_USD:
        return "MOMENTUM_WITH_LIQUIDITY", quote
    return "NO_QUALIFIED_MOMENTUM", quote

def analyze(db, hours=24, as_of=None):
    if hours <= 0 or hours > 744:
        raise ValueError("hours must be in (0,744]")
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = now-timedelta(hours=hours)
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute("""SELECT observed_at,mint,pair,status,price_usd,
                   liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
                   FROM minute_observations
                   WHERE julianday(observed_at)>=julianday(?)
                     AND julianday(observed_at)<=julianday(?)
                   ORDER BY mint,pair,observed_at""",
                   (cutoff.isoformat(),now.isoformat())).fetchall()

    grouped=defaultdict(list)
    for observed,mint,pair,status,price,liq,vol,buys,sells,age in rows:
        stamp=utc(observed)
        if stamp and mint and pair and status=="QUOTE_MARKET_OBSERVED":
            grouped[(mint,pair)].append((stamp,price,liq,vol,buys,sells,age))

    counts=Counter()
    unique=defaultdict(set)
    examples=defaultdict(list)
    evaluated=0
    for (mint,pair),series in grouped.items():
        times=[r[0] for r in series]
        for i,(t,p0,l0,vol,buys,sells,age) in enumerate(series):
            p0,l0,vol,buys,sells,age=map(finite,(p0,l0,vol,buys,sells,age))
            if not (p0 and p0>0 and l0 is not None and l0>=MIN_ENTRY_LIQ_USD
                    and vol is not None and vol>=MIN_ENTRY_VOL_5M
                    and buys is not None and sells is not None and buys>sells
                    and age is not None and 0<=age<=24):
                continue
            target=t+timedelta(minutes=5)
            j=bisect_left(times,target)
            if j>=len(times) or not 0<=(times[j]-target).total_seconds()<=MAX_HORIZON_LAG_SECONDS:
                counts["MISSING_5M_MEASUREMENT"]+=1
                continue
            later=series[j]
            label,quote=classify(p0,later[1],l0,later[2],False)
            evaluated+=1
            counts[label]+=1
            unique[label].add((mint,pair))
            if len(examples[label])<5:
                p1=finite(later[1])
                l1=finite(later[2])
                examples[label].append({"mint":mint,"pair":pair,
                    "baseline_utc":t.isoformat(),"after_5m_utc":later[0].isoformat(),
                    "entry_price_usd":p0,"exit_price_usd":p1,
                    "price_change_pct":round(100*(p1/p0-1),3) if p1 is not None else None,
                    "entry_liquidity_usd":l0,"exit_liquidity_usd":l1,
                    "classification":label,"sell_quote_evidence":quote})
    return {"mode":"ISOLATED_5M_PRICE_LIQUIDITY_CLASSIFICATION_READ_ONLY",
        "as_of_utc":now.isoformat(),"hours":hours,"market_pairs":len(grouped),
        "evaluated_windows":evaluated,"classifications":dict(counts),
        "unique_pairs_by_classification":{k:len(v) for k,v in unique.items()},
        "examples":dict(examples),"quote_verified_count":0,
        "source_db_writes":0,"portfolio_writes":0,"orders_executed":0,
        "limitations":[
          "These are overlapping windows, not independent trades or independent tokens.",
          "Observed_at is minute API fetch time; source_snapshot_at is not DexScreener market tick time.",
          "DexScreener observations do not prove real fills or exit sellability.",
          "No Jupiter quotes are present in this database: all historical prices are unverified.",
          "PRICE_SPIKE_UNVERIFIED is observational and does not approve an entry.",
          "Classification thresholds are research diagnostics, not strategy or security gates.",
          "A future prospective quote-cohort may add verified indicative quote evidence separately."]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--hours",type=int,default=24)
    args=p.parse_args()
    print(json.dumps(analyze(args.db,args.hours),indent=2))
if __name__=="__main__":
    main()
