"""Prospective, isolated GTA6 Discovery Shadow V1.

Compares baseline 30 with expanded <=100 tokens. Reads original learning.db
in SQLite read-only mode; writes ONLY a new shadow SQLite file. Does not send
orders, use Jupiter, touch production states, or change existing timers.
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from research_minute_market_pilot import ORIGINAL, API, source_candidates, extract

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SHADOW=ROOT/"discovery_shadow_v1.sqlite"
BASELINE=30
MAX_CANDIDATES=100
MIN_LIQUIDITY=20000
MIN_VOLUME_M5=250
MIN_SAMPLE_GAP_SEC=45
MAX_SAMPLE_GAP_SEC=600
MIN_VOLUME_GROWTH=1.10

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_samples (
        observed_at TEXT NOT NULL, mint TEXT NOT NULL, pair TEXT NOT NULL,
        source_snapshot_at TEXT NOT NULL, baseline INTEGER NOT NULL,
        status TEXT NOT NULL, price_usd REAL, liquidity_usd REAL,
        volume_m5 REAL, buys_m5 REAL, sells_m5 REAL,
        base_pass INTEGER NOT NULL, momentum_pass INTEGER NOT NULL,
        momentum_reason TEXT NOT NULL, previous_observed_at TEXT,
        PRIMARY KEY(observed_at,mint,pair))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_discovery_pair_time
                  ON discovery_samples(mint,pair,observed_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_signals (
        mint TEXT NOT NULL, pair TEXT NOT NULL,
        first_seen_utc TEXT NOT NULL, baseline INTEGER NOT NULL,
        PRIMARY KEY(mint,pair))""")
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_cycles (
        observed_at TEXT PRIMARY KEY, baseline_selected INTEGER NOT NULL,
        expanded_selected INTEGER NOT NULL, baseline_known INTEGER NOT NULL,
        expanded_known INTEGER NOT NULL, baseline_base INTEGER NOT NULL,
        expanded_base INTEGER NOT NULL, baseline_momentum INTEGER NOT NULL,
        expanded_momentum INTEGER NOT NULL, newly_signaled INTEGER NOT NULL)""")

def fetch_all(session, selected):
    """At most four DexScreener requests for <=100 distinct token addresses."""
    mints=list(dict.fromkeys(m for m,_,_ in selected))
    payload=[]
    for start in range(0,len(mints),30):
        response=session.get(API+",".join(mints[start:start+30]),timeout=20)
        response.raise_for_status()
        chunk=response.json()
        if not isinstance(chunk,list):
            raise ValueError("Unexpected DexScreener response")
        payload.extend(chunk)
    return extract(payload,selected)

def previous_sample(db,mint,pair,stamp):
    return db.execute("""SELECT observed_at,volume_m5,buys_m5,sells_m5
          FROM discovery_samples
          WHERE mint=? AND pair=? AND observed_at<?
          ORDER BY observed_at DESC LIMIT 1""",(mint,pair,stamp)).fetchone()

def momentum(now,previous,volume,buys,sells):
    """Only claim observed m5-volume growth, not token-age or quote execution."""
    if previous is None:
        return False,"AWAITING_SECOND_OBSERVATION",None
    when,old_vol,old_buys,old_sells=previous
    try:
        age=(now-datetime.fromisoformat(when)).total_seconds()
    except (TypeError,ValueError):
        return False,"INVALID_PREVIOUS_TIMESTAMP",when
    if age<MIN_SAMPLE_GAP_SEC:
        return False,"SAMPLES_TOO_CLOSE",when
    if age>MAX_SAMPLE_GAP_SEC:
        return False,"PREVIOUS_SAMPLE_TOO_OLD",when
    if old_vol is None or old_vol<=0 or volume is None:
        return False,"VOLUME_NOT_COMPARABLE",when
    if buys is None or sells is None or buys<=sells:
        return False,"BUY_PRESSURE_NOT_CONFIRMED",when
    if volume<old_vol*MIN_VOLUME_GROWTH:
        return False,"M5_VOLUME_NOT_GROWING",when
    return True,"M5_VOLUME_GROWING_AND_BUY_PRESSURE",when

def classify(found,selected,now,db):
    samples=[]
    for index,(mint,pair,snapshot) in enumerate(selected):
        item=found.get(mint)
        if item is not None and item.get("pair")!=pair:
            item=None
        price=item.get("price") if item else None
        liq=item.get("liquidity") if item else None
        volume=item.get("volume") if item else None
        buys=item.get("buys") if item else None
        sells=item.get("sells") if item else None
        if price is None or price<=0:
            status="MARKET_DATA_UNAVAILABLE"
        elif liq is None:
            status="LIQUIDITY_UNKNOWN"
        else:
            status="QUOTE_MARKET_OBSERVED"
        base=(status=="QUOTE_MARKET_OBSERVED"
              and liq>=MIN_LIQUIDITY and volume is not None and volume>=MIN_VOLUME_M5
              and buys is not None and sells is not None and buys>sells)
        prior=previous_sample(db,mint,pair,now.isoformat(timespec="seconds"))
        is_momentum,reason,prev_stamp=momentum(now,prior,volume,buys,sells)
        samples.append((now.isoformat(timespec="seconds"),mint,pair,snapshot,
                        int(index<BASELINE),status,price,liq,volume,buys,sells,
                        int(base),int(base and is_momentum),reason,prev_stamp))
    return samples

def summarize(samples,baseline_only=False):
    subset=[r for r in samples if not baseline_only or r[4]]
    return {"selected":len(subset),
            "known_liquidity":sum(r[5]=="QUOTE_MARKET_OBSERVED" for r in subset),
            "unknown_liquidity":sum(r[5]=="LIQUIDITY_UNKNOWN" for r in subset),
            "base_pass_without_age_or_B3_momentum":sum(r[11] for r in subset),
            "shadow_volume_momentum_pass":sum(r[12] for r in subset),
            "unique_base_pass_mints":len({r[1] for r in subset if r[11]}),
            "unique_momentum_pass_mints":len({r[1] for r in subset if r[12]})}

def run(original=ORIGINAL,shadow=SHADOW,now=None,session=None,limit=MAX_CANDIDATES):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or not BASELINE<=limit<=MAX_CANDIDATES:
        raise ValueError("UTC-aware timestamp and limit between 30 and 100 required")
    with sqlite3.connect(Path(original).resolve().as_uri()+"?mode=ro",uri=True) as source:
        source.execute("PRAGMA query_only=ON")
        selected=source_candidates(source,now,limit=limit)
    # Ensure unique mint and pair; the selection query normally yields 1/mint.
    selected=list({mint:(mint,pair,source_stamp)
                   for mint,pair,source_stamp in selected}.values())
    def collect(client):
        found=fetch_all(client,selected)
        target=Path(shadow)
        target.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(target) as db:
            initialize(db)
            stamp=now.isoformat(timespec="seconds")
            # Do not use future or same-second data as a previous sample.
            samples=classify(found,selected,now,db)
            for row in samples:
                db.execute("""INSERT OR IGNORE INTO discovery_samples
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",row)
            baseline=summarize(samples,True)
            expanded=summarize(samples,False)
            new_signals=0
            for row in samples:
                if row[12]:
                    before=db.total_changes
                    db.execute("""INSERT OR IGNORE INTO discovery_signals
                                 VALUES(?,?,?,?)""",(row[1],row[2],stamp,row[4]))
                    new_signals+=int(db.total_changes>before)
            db.execute("""INSERT OR IGNORE INTO discovery_cycles
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",(
                stamp,baseline["selected"],expanded["selected"],
                baseline["known_liquidity"],expanded["known_liquidity"],
                baseline["base_pass_without_age_or_B3_momentum"],
                expanded["base_pass_without_age_or_B3_momentum"],
                baseline["shadow_volume_momentum_pass"],
                expanded["shadow_volume_momentum_pass"],new_signals))
            return {"mode":"DISCOVERY_SHADOW_V1_PAPER_ONLY",
                    "observed_utc":stamp,"baseline":baseline,"expanded":expanded,
                    "expanded_minus_baseline":{
                        "known_liquidity":expanded["known_liquidity"]-baseline["known_liquidity"],
                        "base_pass":expanded["base_pass_without_age_or_B3_momentum"]-baseline["base_pass_without_age_or_B3_momentum"],
                        "volume_momentum":expanded["shadow_volume_momentum_pass"]-baseline["shadow_volume_momentum_pass"]},
                    "new_unique_shadow_signals":new_signals,
                    "api_calls":(len(selected)+29)//30,
                    "state":"ISOLATED_SHADOW_SQLITE",
                    "no_orders":True,"original_unchanged":True,
                    "limitations":[
                        "Historical source selection is unchanged; shadow widens monitoring only.",
                        "A first sample cannot satisfy two-observation momentum.",
                        "Rolling 5m-volume growth is a research proxy, not original B3 momentum.",
                        "No age, quote routing, execution slippage or profitability approved.",
                        "Signal identity is mint+pair and de-duplicated in the isolated DB.",
                        "Repeated runs in the same second do not create duplicate samples or signals."]}
    if session is not None:
        return collect(session)
    with requests.Session() as client:
        return collect(client)

def report(shadow=SHADOW):
    path=Path(shadow)
    if not path.exists():
        return {"mode":"DISCOVERY_SHADOW_V1_READ_ONLY_REPORT","status":"NO_SAMPLES"}
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        totals=db.execute("""SELECT COUNT(*),SUM(newly_signaled),
                             SUM(baseline_base),SUM(expanded_base),
                             SUM(baseline_momentum),SUM(expanded_momentum)
                             FROM discovery_cycles""").fetchone()
        unique=db.execute("""SELECT COUNT(*),SUM(CASE WHEN baseline=1 THEN 1 ELSE 0 END)
                             FROM discovery_signals""").fetchone()
    return {"mode":"DISCOVERY_SHADOW_V1_READ_ONLY_REPORT",
            "cycles":totals[0],"new_signal_events_total":totals[1] or 0,
            "sum_baseline_base_per_cycle":totals[2] or 0,
            "sum_expanded_base_per_cycle":totals[3] or 0,
            "sum_baseline_momentum_per_cycle":totals[4] or 0,
            "sum_expanded_momentum_per_cycle":totals[5] or 0,
            "unique_shadow_signal_pairs":unique[0],
            "unique_shadow_signal_pairs_in_baseline_at_first_signal":unique[1] or 0,
            "no_trading_claim":True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original",default=ORIGINAL)
    p.add_argument("--shadow",default=str(SHADOW))
    p.add_argument("--limit",type=int,default=100)
    p.add_argument("--report-only",action="store_true")
    a=p.parse_args()
    result=report(a.shadow) if a.report_only else run(a.original,a.shadow,limit=a.limit)
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
