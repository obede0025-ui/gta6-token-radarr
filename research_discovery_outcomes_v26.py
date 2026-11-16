"""V2.6 prospective 1/5/15m outcome trial; no order placement.

Discovers fresh research signals through isolated V2.2 sampling, freezes
their EXACT mint/pair and signal-time USD price, and monitors market quotes
for 1, 5, 15 minute horizons. Never backfills stale V2.5 signals as if
prospectively observed. Price-only markouts are NOT executable exit quotes.
"""
import argparse
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from research_discovery_quality_v22 import execute
from research_discovery_shadow_v2 import fetch_markets
from research_minute_market_pilot import finite

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SOURCE=ROOT/"discovery_v26_source.sqlite"
OUTCOMES=ROOT/"discovery_v26_outcomes.sqlite"
HORIZONS=(60,300,900)
DISCOVERY_CYCLES=10
TOTAL_CYCLES=25
INTERVAL=60
COST_PP=2.0
MAX_FAILURES=3
MAX_LATENESS=90

def init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS signals(
      mint TEXT NOT NULL,pair TEXT NOT NULL,cohort TEXT NOT NULL,
      signal_utc TEXT NOT NULL,entry_price REAL NOT NULL,
      entry_liquidity REAL NOT NULL,
      PRIMARY KEY(mint,pair,cohort,signal_utc))""")
    db.execute("""CREATE TABLE IF NOT EXISTS followups(
      mint TEXT NOT NULL,pair TEXT NOT NULL,cohort TEXT NOT NULL,
      signal_utc TEXT NOT NULL,horizon_sec INTEGER NOT NULL,
      observed_utc TEXT NOT NULL,elapsed_sec REAL NOT NULL,
      price_usd REAL,liquidity_usd REAL,status TEXT NOT NULL,
      PRIMARY KEY(mint,pair,cohort,signal_utc,horizon_sec))""")

def valid_price(value):
    p=finite(value)
    return p if p is not None and math.isfinite(p) and p>0 else None

def track_new(source, outcomes, stamp):
    """Take ONLY newly written signal rows from this trial's exact cycle."""
    added=0
    with sqlite3.connect(Path(source).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows=db.execute("""SELECT cohort,mint,pair,price_usd,liquidity_usd
          FROM quality_cohort_samples WHERE observed_at=?
            AND momentum_pass=1""",(stamp,)).fetchall()
    for cohort,mint,pair,price,liq in rows:
        price=valid_price(price)
        liq=finite(liq)
        if price is None or liq is None or liq<20000:continue
        before=outcomes.total_changes
        outcomes.execute("""INSERT OR IGNORE INTO signals
          VALUES(?,?,?,?,?,?)""",(mint,pair,cohort,stamp,price,liq))
        added+=int(outcomes.total_changes>before)
    outcomes.commit()
    return added

def pending(outcomes,now):
    """Only horizons due at/after target and not more than 90s late."""
    rows=outcomes.execute("""SELECT s.mint,s.pair,s.cohort,s.signal_utc,
        s.entry_price,s.entry_liquidity
        FROM signals s""").fetchall()
    due=[];missed=[]
    for mint,pair,cohort,stamp,entry,liq in rows:
        signal_time=datetime.fromisoformat(stamp)
        elapsed=(now-signal_time).total_seconds()
        for horizon in HORIZONS:
            if elapsed<horizon:continue
            exists=outcomes.execute("""SELECT 1 FROM followups
              WHERE mint=? AND pair=? AND cohort=? AND signal_utc=?
              AND horizon_sec=?""",(mint,pair,cohort,stamp,horizon)).fetchone()
            if exists:continue
            rec=(mint,pair,cohort,stamp,horizon,elapsed)
            if elapsed>horizon+MAX_LATENESS:missed.append(rec)
            else:due.append(rec)
    return due,missed

def store_observations(outcomes,now,session):
    due,missed=pending(outcomes,now)
    stamp=now.isoformat(timespec="seconds")
    if not due and not missed:return {"due":0,"missed":0,"network_batches":0}
    exact={}
    batches=0
    if due:
        mints=list(dict.fromkeys(r[0] for r in due))
        markets=fetch_markets(session,mints)
        batches=(len(mints)+29)//30
        for market in markets:
            if not isinstance(market,dict) or market.get("chainId")!="solana":continue
            mint=(market.get("baseToken") or {}).get("address")
            pair=market.get("pairAddress")
            if (mint,pair) in {(r[0],r[1]) for r in due}:
                exact[(mint,pair)]=market
    for rec in due+missed:
        mint,pair,cohort,signal,horizon,elapsed=rec
        market=exact.get((mint,pair)) if rec in due else None
        price=valid_price(market.get("priceUsd")) if market else None
        liq=finite((market.get("liquidity") or {}).get("usd")) if market else None
        if liq is not None and liq<0:liq=None
        status=("HORIZON_MISSED" if rec in missed else
                "PAIR_NOT_RETURNED" if market is None else
                "PRICE_UNAVAILABLE" if price is None else
                "LIQUIDITY_UNKNOWN" if liq is None else
                "LIQUIDITY_BELOW_20000" if liq<20000 else
                "MARKET_PRICE_OBSERVED")
        outcomes.execute("""INSERT OR IGNORE INTO followups
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (mint,pair,cohort,signal,horizon,stamp,elapsed,price,liq,status))
    outcomes.commit()
    return {"due":len(due),"missed":len(missed),"network_batches":batches}

def report(source=SOURCE,outcomes=OUTCOMES,errors=None,cycles=0):
    path=Path(outcomes)
    result={"mode":"DISCOVERY_V26_PROSPECTIVE_OUTCOMES",
            "cycles_completed":cycles,"errors":errors or [],
            "note":"Price markouts only; no demonstrated sellability or executable exit.",
            "assumed_roundtrip_cost_percentage_points":COST_PP,
            "horizons_seconds":list(HORIZONS),"signals":[]}
    if not path.is_file():
        result["status"]="NO_OUTCOME_DATABASE";return result
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        signals=db.execute("""SELECT mint,pair,cohort,signal_utc,
            entry_price,entry_liquidity FROM signals
            ORDER BY signal_utc,mint,cohort""").fetchall()
        result["unique_exact_pairs"]=len({(r[0],r[1]) for r in signals})
        result["cohort_signal_records"]=len(signals)
        totals={str(h):{"priced":0,"missing_or_unverified":0} for h in HORIZONS}
        for mint,pair,cohort,stamp,entry,entryliq in signals:
            rows=db.execute("""SELECT horizon_sec,observed_utc,elapsed_sec,
                price_usd,liquidity_usd,status FROM followups
                WHERE mint=? AND pair=? AND cohort=? AND signal_utc=?""",
                (mint,pair,cohort,stamp)).fetchall()
            by_h={r[0]:r for r in rows}
            outcomes_by_h={}
            for h in HORIZONS:
                row=by_h.get(h)
                if row is None:
                    outcomes_by_h[str(h)]={"status":"NOT_YET_MEASURED"}
                    totals[str(h)]["missing_or_unverified"]+=1
                    continue
                _,observed,elapsed,price,liq,status=row
                gross=round(100*(price/entry-1),3) if price is not None else None
                net=round(gross-COST_PP,3) if gross is not None else None
                outcomes_by_h[str(h)]={
                    "status":status,"observed_utc":observed,
                    "elapsed_seconds":elapsed,"market_price_usd":price,
                    "liquidity_usd":liq,"gross_price_change_pct":gross,
                    "net_hypothetical_pct_after_cost":net,
                    "executable_exit_verified":False}
                if status=="MARKET_PRICE_OBSERVED":
                    totals[str(h)]["priced"]+=1
                else:totals[str(h)]["missing_or_unverified"]+=1
            result["signals"].append({"mint":mint,"pair":pair,"cohort":cohort,
                "signal_utc":stamp,"entry_market_price_usd":entry,
                "entry_liquidity_usd":entryliq,"outcomes":outcomes_by_h})
        result["horizon_coverage"]=totals
    result["status"]="OK" if result["signals"] else "NO_NEW_SIGNALS"
    result["safety"]={"trades":0,"production_changes":0,"production_db_writes":0,
                       "fixed_cost_model_not_actual_slippage":True}
    result["limitations"]=[
        "Old V2.5 signals are not backfilled; this requires fresh prospective signals.",
        "DexScreener priceUsd is a market-price proxy, not a sell-side executable quote.",
        "Net hypothetical percent subtracts a flat 2 percentage-point assumption.",
        "Pair liquidity does not prove size-specific exit liquidity.",
        "Missing observations, expired horizons or missing pools are not profitable outcomes.",
        "One manual trial with promoted discovery feeds is not evidence of repeatable profits."]
    return result

def run(source=SOURCE,outcomes=OUTCOMES,cycles=TOTAL_CYCLES,
        discovery_cycles=DISCOVERY_CYCLES,interval=INTERVAL,
        now_fn=lambda:datetime.now(timezone.utc),
        sleep_fn=time.sleep,clock_fn=time.monotonic,
        sample_fn=execute,session=None):
    if not 20<=cycles<=30 or not 2<=discovery_cycles<=10 or discovery_cycles>cycles-15:
        raise ValueError("cycles must be 20..30 and leave >=15 cycles after discovery")
    if interval!=60:raise ValueError("prospective horizon protocol requires 60 sec")
    source=Path(source);outcomes=Path(outcomes)
    if source.resolve()==outcomes.resolve():raise ValueError("source and outcome DB must differ")
    # A fresh source database is required: prohibit silently including past
    # V2.5 samples and inconsistent old trial anchors.
    if source.exists() or outcomes.exists():
        raise FileExistsError("V2.6 trial files already exist; use --report-only or choose NEW --source/--outcomes paths; never delete databases")
    source.parent.mkdir(parents=True,exist_ok=True)
    outcomes.parent.mkdir(parents=True,exist_ok=True)
    errors=[];failure_streak=0;finished=0;start=clock_fn()
    owns_session=session is None
    client=session or requests.Session()
    try:
        with sqlite3.connect(outcomes) as db:
            init(db)
            for i in range(cycles):
                remain=start+i*interval-clock_fn()
                if remain>0:sleep_fn(remain)
                now=now_fn()
                try:
                    if i<discovery_cycles:
                        answer=sample_fn(shadow=source,now=now,slots=20)
                        if answer.get("status")!="RECORDED":
                            raise RuntimeError("V2.2 discovery status "+str(answer.get("status")))
                        track_new(source,db,answer["observed_utc"])
                    store_observations(db,now_fn(),client)
                    failure_streak=0
                except Exception as exc:
                    errors.append({"cycle":i+1,"type":type(exc).__name__,
                                   "message":str(exc)[:240]})
                    failure_streak+=1
                    if failure_streak>=MAX_FAILURES:break
                finished+=1
                # No compressed catch-up requests when a cycle runs slowly.
                lag=clock_fn()-(start+(i+1)*interval)
                if lag>0:start=clock_fn()-i*interval
    finally:
        if owns_session:client.close()
    return report(source,outcomes,errors,finished)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",default=str(SOURCE))
    p.add_argument("--outcomes",default=str(OUTCOMES))
    p.add_argument("--report-only",action="store_true")
    a=p.parse_args()
    result=report(a.source,a.outcomes) if a.report_only else run(a.source,a.outcomes)
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
