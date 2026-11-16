"""Discovery V2.3: read-only diagnosis of prospective V2.2 momentum failures.

Only examines already-collected cohort records. No HTTP, database writes,
new signals, trades, production changes or state resets.
"""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from research_discovery_quality_v22 import SHADOW
from research_discovery_shadow_v2 import MIN_GAP, MAX_GAP, MOMENTUM_RATIO

def previous(db, stamp, mint, pair, cohort):
    return db.execute("""SELECT observed_at,volume_m5,buys_m5,sells_m5
        FROM quality_cohort_samples
        WHERE mint=? AND pair=? AND cohort=? AND observed_at<?
        ORDER BY observed_at DESC LIMIT 1""",
        (mint,pair,cohort,stamp)).fetchone()

def diagnose(current, old):
    stamp,mint,pair,cohort,feed,price,liq,vol,buys,sells,age,status,base,mom,reason=current
    delta=None;growth=None;previous_volume=None
    if old:
        previous_volume=old[1]
        try:
            delta=(datetime.fromisoformat(stamp)-datetime.fromisoformat(old[0])).total_seconds()
        except (ValueError,TypeError):
            pass
        if vol is not None and previous_volume is not None and previous_volume>0:
            growth=round((vol/previous_volume-1)*100,2)
    blockers=[]
    if not base:
        if status=="MARKET_DATA_UNAVAILABLE":blockers.append("PRICE_OR_PAIR_UNAVAILABLE")
        if liq is None:blockers.append("LIQUIDITY_UNKNOWN")
        elif liq<20000:blockers.append("LIQUIDITY_BELOW_20000")
        if vol is None:blockers.append("VOLUME_UNKNOWN")
        elif vol<250:blockers.append("VOLUME_BELOW_250")
        if buys is None or sells is None:blockers.append("BUY_SELL_UNKNOWN")
        elif buys<=sells:blockers.append("BUY_PRESSURE_WEAK")
        if age is None:blockers.append("PAIR_AGE_UNKNOWN")
        elif not 0<=age<=24:blockers.append("PAIR_AGE_OUTSIDE_0_24H")
    momentum_blockers=[]
    if base and not mom:
        if old is None:momentum_blockers.append("NO_PRIOR_SELECTED_PAIR_SAMPLE")
        elif delta is None:momentum_blockers.append("INVALID_TIME_GAP")
        elif delta<MIN_GAP:momentum_blockers.append("SAMPLES_TOO_CLOSE")
        elif delta>MAX_GAP:momentum_blockers.append("PRIOR_SAMPLE_TOO_OLD")
        elif growth is None:momentum_blockers.append("VOLUME_NOT_COMPARABLE")
        else:
            if growth<(MOMENTUM_RATIO-1)*100-1e-8:
                momentum_blockers.append("INSUFFICIENT_VOLUME_GROWTH")
            if buys is None or sells is None or buys<=sells:
                momentum_blockers.append("BUY_PRESSURE_NOT_CONFIRMED")
        if not momentum_blockers:
            momentum_blockers.append("RECORDED_MOMENTUM_REASON_"+str(reason))
    return {"mint":mint,"pair":pair,"cohort":cohort,"observed_utc":stamp,
            "base_pass":bool(base),"momentum_pass":bool(mom),
            "liquidity_usd":liq,"volume_m5":vol,"previous_volume_m5":previous_volume,
            "volume_growth_pct":growth,"buys_m5":buys,"sells_m5":sells,
            "buy_sell_ratio":round(buys/sells,2) if buys is not None and sells is not None and sells>0 else None,
            "gap_seconds":delta,"momentum_reason_recorded":reason,
            "base_blockers":blockers,"momentum_blockers":momentum_blockers}

def analyse(db_path=SHADOW,latest_cycles=3,examples_per_cohort=6):
    if not 1<=latest_cycles<=1000 or not 0<=examples_per_cohort<=30:
        raise ValueError("latest_cycles 1..1000; examples 0..30")
    if not Path(db_path).is_file():
        return {"mode":"DISCOVERY_V23_MOMENTUM_DIAGNOSIS","status":"NO_DATABASE"}
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        timestamps=[r[0] for r in db.execute("""
            SELECT observed_at FROM quality_cycles
            ORDER BY observed_at DESC LIMIT ?""",(latest_cycles,))]
        if not timestamps:
            return {"mode":"DISCOVERY_V23_MOMENTUM_DIAGNOSIS","status":"NO_CYCLES"}
        placeholders=",".join("?" for _ in timestamps)
        rows=db.execute(f"""SELECT observed_at,mint,pair,cohort,feed,price_usd,
            liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours,status,
            base_pass,momentum_pass,momentum_reason
            FROM quality_cohort_samples
            WHERE observed_at IN ({placeholders})
            ORDER BY observed_at DESC,cohort,mint,pair""",timestamps).fetchall()
        bycohort={}
        for cohort in ("FEED_ORDER","ACTIVITY_FIRST"):
            subset=[r for r in rows if r[3]==cohort]
            inspected=[diagnose(row,previous(db,row[0],row[1],row[2],row[3])) for row in subset]
            blockers=Counter(b for item in inspected for b in item["base_blockers"])
            mom_blockers=Counter(b for item in inspected for b in item["momentum_blockers"])
            relevant=[item for item in inspected if item["base_pass"]]
            bycohort[cohort]={
                "observations":len(subset),
                "unique_mints":len({r[1] for r in subset}),
                "base_pass_observations":sum(x["base_pass"] for x in inspected),
                "momentum_pass_observations":sum(x["momentum_pass"] for x in inspected),
                "base_blockers_overlapping":dict(blockers.most_common()),
                "momentum_blockers_for_base_pass_overlapping":dict(mom_blockers.most_common()),
                "base_pass_examples":relevant[:examples_per_cohort]}
    return {"mode":"DISCOVERY_V23_MOMENTUM_DIAGNOSIS","status":"OK",
            "cycles_reviewed":len(timestamps),"first_cycle_utc":min(timestamps),
            "last_cycle_utc":max(timestamps),"cohorts":bycohort,
            "safety":{"network_requests":0,"database_writes":0,"trades":0},
            "limitations":[
                "Rejection reasons overlap and are observation counts, not unique failed tokens.",
                "Volume growth compares rolling 5m windows; a positive change is not proof of net inflow.",
                "Previous sample must belong to the same cohort and exact pair.",
                "No real trades, executable quotes, token safety or complete B3 approval."]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default=str(SHADOW))
    p.add_argument("--cycles",type=int,default=3)
    p.add_argument("--examples",type=int,default=6)
    args=p.parse_args()
    print(json.dumps(analyse(args.db,args.cycles,args.examples),indent=2))
if __name__=="__main__":
    main()
