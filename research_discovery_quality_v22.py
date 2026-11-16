"""Discovery V2.2: prospective activity-first vs feed-order cohort comparison.

Select both cohorts using only *previously recorded* token measurements,
before requesting current market data. No same-snapshot ranking advantage.
The independent token profile/boost feeds are promotional and incomplete.
One-shot/manual, isolated sqlite only, no trading or production updates.
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import requests

from research_discovery_shadow_v2 import (
    PROFILE_URL, BOOST_URL, SHADOW as V2_DB, discover,
    choose_pairs, fetch_list, fetch_markets, make_sample, count
)
from research_minute_market_pilot import finite

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SHADOW=ROOT/"discovery_quality_v22.sqlite"
MAX_UNIVERSE=60
DEFAULT_SLOTS=20

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS quality_market_samples (
      observed_at TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,
      liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
      pair_age_hours REAL,price_usd REAL,
      PRIMARY KEY(observed_at,mint,pair))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_quality_history
      ON quality_market_samples(mint,observed_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS quality_cohort_samples (
      observed_at TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,
      cohort TEXT NOT NULL,feed TEXT NOT NULL,price_usd REAL,
      liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
      pair_age_hours REAL,status TEXT NOT NULL,base_pass INTEGER NOT NULL,
      momentum_pass INTEGER NOT NULL,momentum_reason TEXT NOT NULL,
      PRIMARY KEY(observed_at,mint,pair,cohort))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_quality_cohort_time
      ON quality_cohort_samples(mint,pair,cohort,observed_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS quality_signals (
      cohort TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,
      first_seen_utc TEXT NOT NULL,PRIMARY KEY(cohort,mint,pair))""")
    db.execute("""CREATE TABLE IF NOT EXISTS quality_cycles (
      observed_at TEXT PRIMARY KEY,universe_size INTEGER,slots INTEGER,
      feed_selected INTEGER,quality_selected INTEGER,rotated_slots INTEGER,
      feed_known INTEGER,quality_known INTEGER,feed_base INTEGER,
      quality_base INTEGER,feed_momentum INTEGER,quality_momentum INTEGER,
      new_feed_signals INTEGER,new_quality_signals INTEGER,
      ranked_with_history INTEGER)""")

def history(db,mints,now):
    """Latest previous sample per mint, strict timestamp cutoff; no future leakage."""
    result={}
    stamp=now.isoformat(timespec="seconds")
    for mint in mints:
        row=db.execute("""SELECT observed_at,pair,liquidity_usd,volume_m5,
            buys_m5,sells_m5,pair_age_hours,price_usd
            FROM quality_market_samples
            WHERE mint=? AND observed_at<? ORDER BY observed_at DESC LIMIT 1""",
            (mint,stamp)).fetchone()
        if row:result[mint]=row
    return result

def rank_from_history(mints,previous,slots):
    """Use previously seen activity and liquidity to prioritize monitoring.

    Scores do NOT approve a trade, and missing history retains feed order.
    """
    def priority(mint):
        row=previous.get(mint)
        if row is None:return (2,0,0,0)
        _,pair,liq,vol,buys,sells,age,price=row
        qualified=(price is not None and price>0 and
                   liq is not None and liq>=20000 and
                   vol is not None and vol>=250 and
                   buys is not None and sells is not None and buys>sells and
                   age is not None and 0<=age<=24)
        active=(vol is not None and vol>=250 and
                buys is not None and sells is not None and buys>sells)
        # Known complete market evidence first, then volume (bounded rank
        # comparison only). Stale data not treated as fresh approval.
        return (0 if qualified else 1 if active else 2,
                -(vol or 0),-(buys-sells if buys is not None and sells is not None else 0),
                -(liq or 0))
    ordered=sorted(enumerate(mints),key=lambda v:(priority(v[1]),v[0]))
    return [mint for _,mint in ordered[:slots]]

def market_sample(now,mint,p):
    """Exact selected pair attributes; unknown is retained as None."""
    from research_discovery_shadow_v2 import make_sample
    # Reuse calculation with an in-memory, no-prior database only for fields;
    # do not persist this provisional row or claim momentum.
    # Inline safe extraction here to avoid side effects.
    tx=(p.get("txns") or {}).get("m5") or {}
    created=finite(p.get("pairCreatedAt"))
    age=(now.timestamp()*1000-created)/3600000 if created and created>0 else None
    if age is not None and age<0:age=None
    return (now.isoformat(timespec="seconds"),mint,p.get("pairAddress"),
            finite((p.get("liquidity") or {}).get("usd")),
            finite((p.get("volume") or {}).get("m5")),
            finite(tx.get("buys")),finite(tx.get("sells")),age,
            finite(p.get("priceUsd")))

def execute(shadow=SHADOW,now=None,session=None,slots=DEFAULT_SLOTS):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 1<=slots<=MAX_UNIVERSE:
        raise ValueError("aware timestamp and slots 1..60 required")
    def cycle(client):
        profiles=fetch_list(client,PROFILE_URL)
        boosts=fetch_list(client,BOOST_URL)
        candidates=discover(profiles,boosts,limit=MAX_UNIVERSE)
        mints=list(candidates)
        target=Path(shadow)
        target.parent.mkdir(parents=True,exist_ok=True)
        # Snapshot selection occurs BEFORE current market HTTP calls;
        # a successful HTTP call is mandatory before recording any cycle.
        with sqlite3.connect(target) as db:
            initialize(db)
            db.execute("BEGIN IMMEDIATE")
            prev=history(db,mints,now)
            a=mints[:slots]
            b=rank_from_history(mints,prev,slots)
            payload=fetch_markets(client,mints)
            selected=choose_pairs(payload,set(mints))
            stamp=now.isoformat(timespec="seconds")
            existing=db.execute("SELECT 1 FROM quality_cycles WHERE observed_at=?",
                                (stamp,)).fetchone()
            if existing:
                db.rollback()
                return {"mode":"DISCOVERY_V22_QUALITY_FIRST_SHADOW",
                        "status":"DUPLICATE_SECOND_SKIPPED","observed_utc":stamp}
            # Samples for BOTH cohorts use the same captured market payload.
            cohort_rows={}
            for cohort,chosen in (("FEED_ORDER",a),("ACTIVITY_FIRST",b)):
                cohort_rows[cohort]=[
                    make_sample(db,now,mint,selected[mint]["pairAddress"],cohort,
                                candidates[mint],selected[mint])
                    for mint in chosen if mint in selected]
            for cohort in ("FEED_ORDER","ACTIVITY_FIRST"):
                for row in cohort_rows[cohort]:
                    db.execute("INSERT INTO quality_cohort_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
            for mint,p in selected.items():
                row=market_sample(now,mint,p)
                db.execute("INSERT OR IGNORE INTO quality_market_samples VALUES(?,?,?,?,?,?,?,?,?)",row)
            added={}
            for cohort in ("FEED_ORDER","ACTIVITY_FIRST"):
                new=0
                for row in cohort_rows[cohort]:
                    if row[13]:
                        before=db.total_changes
                        db.execute("INSERT OR IGNORE INTO quality_signals VALUES(?,?,?,?)",
                                   (cohort,row[1],row[2],stamp))
                        new+=int(db.total_changes>before)
                added[cohort]=new
            ac=count(cohort_rows["FEED_ORDER"])
            bc=count(cohort_rows["ACTIVITY_FIRST"])
            db.execute("INSERT INTO quality_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                stamp,len(mints),slots,len(cohort_rows["FEED_ORDER"]),
                len(cohort_rows["ACTIVITY_FIRST"]),len(set(b)-set(a)),
                ac["known_liquidity"],bc["known_liquidity"],
                ac["base_pass_including_pair_age"],bc["base_pass_including_pair_age"],
                ac["spaced_momentum_pass"],bc["spaced_momentum_pass"],
                added["FEED_ORDER"],added["ACTIVITY_FIRST"],len(prev)))
            return {"mode":"DISCOVERY_V22_QUALITY_FIRST_SHADOW",
                    "status":"RECORDED","observed_utc":stamp,
                    "universe":len(mints),"slots_per_cohort":slots,
                    "selection_changed":len(set(b)-set(a)),
                    "had_previous_market_evidence":len(prev),
                    "feed_order":ac,"activity_first":bc,
                    "new_unique_signals":{"feed_order":added["FEED_ORDER"],
                                          "activity_first":added["ACTIVITY_FIRST"]},
                    "api_calls":{"discovery_feeds":2,"token_batches":(len(mints)+29)//30},
                    "safe":{"production_changes":0,"trades":0,"independent_db":str(target)},
                    "limitations":[
                        "Ranking uses previous market observations only; no current-market ranking leakage.",
                        "Both cohorts share current API payload, but feed universe can change each run.",
                        "First run has no history and rankings are identical.",
                        "Pool and mint risk, real quotes, B3 momentum and execution are unverified.",
                        "Paid promotion feeds are not representative of all token launches."]}
    if session is not None:return cycle(session)
    with requests.Session() as client:return cycle(client)

def report(shadow=SHADOW):
    path=Path(shadow)
    if not path.is_file():
        return {"mode":"DISCOVERY_V22_QUALITY_FIRST_REPORT","status":"NO_SAMPLES"}
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        cycles=db.execute("""SELECT COUNT(*),SUM(rotated_slots),SUM(feed_base),
            SUM(quality_base),SUM(feed_momentum),SUM(quality_momentum),
            SUM(new_feed_signals),SUM(new_quality_signals),
            MAX(observed_at) FROM quality_cycles""").fetchone()
        unique=dict(db.execute("SELECT cohort,COUNT(*) FROM quality_signals GROUP BY cohort"))
    return {"mode":"DISCOVERY_V22_QUALITY_FIRST_REPORT",
            "cycles":cycles[0],"rotated_slots_total":cycles[1] or 0,
            "base_pass_observations":{"feed_order":cycles[2] or 0,
                                      "activity_first":cycles[3] or 0},
            "momentum_pass_observations":{"feed_order":cycles[4] or 0,
                                          "activity_first":cycles[5] or 0},
            "new_signal_events":{"feed_order":cycles[6] or 0,
                                 "activity_first":cycles[7] or 0},
            "unique_signal_pairs":unique,"latest_cycle_utc":cycles[8],
            "research_only":True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shadow",default=str(SHADOW))
    p.add_argument("--slots",type=int,default=20)
    p.add_argument("--report-only",action="store_true")
    args=p.parse_args()
    print(json.dumps(report(args.shadow) if args.report_only else execute(
        shadow=args.shadow,slots=args.slots),indent=2))
if __name__=="__main__":main()
