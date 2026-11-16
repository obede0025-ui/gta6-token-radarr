"""Discovery V2.4: offline early-activity research, without weakening B3 gates.

Evaluates successive rolling-5m snapshots in existing V2.2 quality cohorts.
A potential early-watch indicator is NOT a momentum approval or a trading signal.
No API calls, database writes, orders, state resets, or timers.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_discovery_quality_v22 import SHADOW

MIN_GAP=45
MAX_GAP=600
MIN_BUY_SHARE=0.65
BUY_COUNT_GROWTH=0.15
MAX_VOLUME_DECLINE=0.15

def parse_time(stamp):
    try:
        dt=datetime.fromisoformat(stamp)
        return dt if dt.tzinfo else None
    except (ValueError,TypeError):
        return None

def snapshot(row):
    # (observed_at,mint,pair,cohort,price_usd,liquidity_usd,
    #  volume_m5,buys_m5,sells_m5,pair_age_hours,base_pass,momentum_pass)
    return {"timestamp":row[0],"volume":row[6],"buys":row[7],
            "sells":row[8],"base_pass":bool(row[10]),
            "momentum_pass":bool(row[11])}

def compare(current,previous,older=None):
    """Same exact mint+pair+cohort. No future data and no inferred trade flow."""
    now=parse_time(current["timestamp"])
    prev=parse_time(previous["timestamp"]) if previous else None
    gap=(now-prev).total_seconds() if now and prev else None
    vol=current["volume"];oldvol=previous["volume"] if previous else None
    buys=current["buys"];sells=current["sells"]
    oldbuys=previous["buys"] if previous else None
    growth=100*(vol/oldvol-1) if vol is not None and oldvol is not None and oldvol>0 else None
    buy_change=100*(buys/oldbuys-1) if buys is not None and oldbuys is not None and oldbuys>0 else None
    share=buys/(buys+sells) if buys is not None and sells is not None and buys+sells>0 else None
    prior_older_valid=False
    if older and prev:
        oldest=parse_time(older["timestamp"])
        prior_gap=(prev-oldest).total_seconds() if oldest else None
        prior_older_valid=prior_gap is not None and MIN_GAP<=prior_gap<=MAX_GAP
    diagnostics=[]
    if not current["base_pass"]:diagnostics.append("BASE_GATE_NOT_MET")
    if previous is None:diagnostics.append("FIRST_OBSERVATION")
    elif gap is None or not MIN_GAP<=gap<=MAX_GAP:diagnostics.append("INVALID_PAIR_INTERVAL")
    if vol is None or oldvol is None or oldvol<=0:diagnostics.append("VOLUME_UNCOMPARABLE")
    if buys is None or sells is None or oldbuys is None or oldbuys<=0:diagnostics.append("BUY_COUNTS_UNCOMPARABLE")
    if share is None or share<MIN_BUY_SHARE:diagnostics.append("INSUFFICIENT_BUY_SHARE")
    if buy_change is None or buy_change<BUY_COUNT_GROWTH*100:diagnostics.append("BUY_COUNT_NOT_ACCELERATING")
    if growth is None or growth<-MAX_VOLUME_DECLINE*100:diagnostics.append("ROLLING_VOLUME_FALLING_FAST")
    # Two snapshots permit a provisional early-watch. Three spaced snapshots
    # required for stronger persistence, but STILL not an approved signal.
    watch=not diagnostics
    persist=watch and prior_older_valid and older is not None and (
        older["base_pass"] and previous["base_pass"])
    return {
        "watch_only":bool(watch),"three_sample_persistence":bool(persist),
        "current_base_pass":current["base_pass"],
        "current_v22_momentum_pass":current["momentum_pass"],
        "sample_gap_sec":gap,
        "volume_growth_pct":round(growth,2) if growth is not None else None,
        "rolling_buy_count_change_pct":round(buy_change,2) if buy_change is not None else None,
        "current_buy_share_pct":round(share*100,2) if share is not None else None,
        "reasons":diagnostics
    }

def analyse(db_path=SHADOW,max_rows=10000,example_limit=5):
    if not 1<=max_rows<=100000 or not 0<=example_limit<=30:
        raise ValueError("max_rows 1..100000 and example_limit 0..30")
    path=Path(db_path)
    if not path.is_file():
        return {"mode":"DISCOVERY_V24_EARLY_ACTIVITY_AUDIT","status":"NO_DATABASE"}
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows=db.execute("""SELECT observed_at,mint,pair,cohort,price_usd,
            liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours,
            base_pass,momentum_pass
            FROM quality_cohort_samples ORDER BY observed_at DESC LIMIT ?""",
            (max_rows,)).fetchall()
    if not rows:
        return {"mode":"DISCOVERY_V24_EARLY_ACTIVITY_AUDIT","status":"NO_SAMPLES"}
    groups=defaultdict(list)
    for r in rows:
        groups[(r[1],r[2],r[3])].append(snapshot(r))
    summaries={}
    for cohort in ("FEED_ORDER","ACTIVITY_FIRST"):
        cases=[]
        for (mint,pair,arm),history in groups.items():
            if arm!=cohort:continue
            ordered=sorted(history,key=lambda p:p["timestamp"])
            # Most recent state per exact pair; first observation is never
            # treated as a confirmed trend.
            current=ordered[-1]
            previous=ordered[-2] if len(ordered)>=2 else None
            older=ordered[-3] if len(ordered)>=3 else None
            check=compare(current,previous,older)
            cases.append({"mint":mint,"pair":pair,"last_utc":current["timestamp"],**check})
        cases.sort(key=lambda x:(not x["three_sample_persistence"],
                                  not x["watch_only"],x["mint"]))
        counts=Counter(reason for case in cases for reason in case["reasons"])
        summaries[cohort]={
            "unique_exact_pairs_examined":len(cases),
            "currently_base_qualified":sum(x["current_base_pass"] for x in cases),
            "current_original_v22_proxy_pass":sum(x["current_v22_momentum_pass"] for x in cases),
            "early_watch_only_pairs":sum(x["watch_only"] for x in cases),
            "persistent_3_sample_watch_pairs":sum(x["three_sample_persistence"] for x in cases),
            "reason_counts_overlapping":dict(counts.most_common()),
            "qualified_or_watch_examples":[x for x in cases
                if x["current_base_pass"] or x["watch_only"]][:example_limit]
        }
    return {"mode":"DISCOVERY_V24_EARLY_ACTIVITY_AUDIT",
            "status":"OK","samples_scanned":len(rows),
            "first_observation_utc":min(r[0] for r in rows),
            "last_observation_utc":max(r[0] for r in rows),
            "cohorts":summaries,
            "recommendation":"Do not change momentum gates: validate early-watch proxies prospectively against later independent quote outcomes.",
            "safety":{"http_requests":0,"writes":0,"trades":0,"production_changes":0},
            "limitations":[
                "Retrospective descriptive audit, not a prospective trading strategy evaluation.",
                "Each rolling-5m buy count contains overlapping windows: change is not new buys or order flow.",
                "Only selected samples in the same cohort and exact pair are compared.",
                "Early-watch is NOT B3 approval, executable liquidity, or a buy recommendation.",
                "One recent state per pair is counted, not independent observations."]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",default=str(SHADOW))
    p.add_argument("--max-rows",type=int,default=10000)
    p.add_argument("--examples",type=int,default=5)
    a=p.parse_args()
    print(json.dumps(analyse(a.db,a.max_rows,a.examples),indent=2))
if __name__=="__main__":
    main()
