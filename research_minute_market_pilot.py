"""Isolated minute-level market measurements for GTA6 paper research.

Reads existing learning.db; polls Dexscreener once per batch; writes ONLY a
separate pilot SQLite file. No orders, wallet, original-state/database writes.
Not yet a B3/M1 signal source: verify coverage and latency before integrating.
"""
import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ORIGINAL = "/home/ubuntu/gta6-paper-vps/data/learning.db"
PILOT = "/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
API = "https://api.dexscreener.com/tokens/v1/solana/"
MAX_TOKENS = 30

def finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None

def source_candidates(conn, now, limit=MAX_TOKENS):
    """Prioritize recent/active original pairs. Every requested pair is pinned."""
    cutoff = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    # Monitoring eligibility is NOT entry eligibility. Keep following tokens
    # after their liquidity or volume drops. Favor tokens that qualified
    # during the last 24h, then prioritize recent pools and market activity.
    history_cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = conn.execute("""
        WITH latest AS (
          SELECT token_address, pair_address, scanned_at, volume_m5,
                 liquidity_usd, pair_age_hours,
                 ROW_NUMBER() OVER (
                    PARTITION BY token_address
                    ORDER BY scanned_at DESC, pair_address DESC
                 ) rn
          FROM snapshots WHERE scanned_at >= ?
        ),
        previously_qualified AS (
          SELECT DISTINCT token_address
          FROM snapshots
          WHERE scanned_at >= ?
            AND pair_age_hours BETWEEN 0 AND 24
            AND volume_m5 >= 250
            AND liquidity_usd >= 20000
        )
        SELECT l.token_address, l.pair_address, l.scanned_at
        FROM latest l
        LEFT JOIN previously_qualified p
          ON p.token_address=l.token_address
        WHERE l.rn=1 AND l.pair_address IS NOT NULL
        ORDER BY
          CASE WHEN p.token_address IS NULL THEN 1 ELSE 0 END,
          CASE WHEN l.pair_age_hours BETWEEN 0 AND 24 THEN 0 ELSE 1 END,
          COALESCE(l.volume_m5,0) DESC,
          COALESCE(l.liquidity_usd,0) DESC,
          l.scanned_at DESC,
          l.token_address
        LIMIT ?
    """, (cutoff, history_cutoff, limit)).fetchall()
    return [(r[0],r[1],r[2]) for r in rows]

def adaptive_candidate_preview(source, pilot, now, limit=MAX_TOKENS):
    """READ-ONLY shadow selection, not yet used by the running collector.

    Preserve a baseline of previously selected pairs while reserving room for
    fresh market candidates. Penalize only pairs with repeatedly UNKNOWN
    liquidity and no recent activity. UNKNOWN never becomes liquidity=0.
    """
    from collections import Counter
    expanded=source_candidates(source, now, limit=max(150,limit*5))
    if not expanded:
        return {"legacy":[],"proposed":[],"diagnostics":{}}
    legacy=expanded[:limit]
    pairs={(mint,pair) for mint,pair,_ in expanded}
    cutoff=(now-timedelta(hours=6)).isoformat(timespec="seconds")
    stats={}
    # Read-only pilot evidence, bounded to the last six hours.
    rows=pilot.execute("""
        SELECT mint,pair,status,volume_m5,observed_at
        FROM minute_observations
        WHERE observed_at>=?
        ORDER BY observed_at DESC
    """,(cutoff,)).fetchall()
    for mint,pair,status,vol,stamp in rows:
        k=(mint,pair)
        if k not in pairs:continue
        x=stats.setdefault(k,{"total":0,"unknown":0,"known":0,
                              "last_volume":None,"last_status":None})
        if x["total"]==0:
            x["last_volume"]=vol
            x["last_status"]=status
        x["total"]+=1
        x["unknown"]+=(status=="LIQUIDITY_UNKNOWN")
        x["known"]+=(status=="QUOTE_MARKET_OBSERVED")
    # Strong evidence of consistently missing liquidity + currently inactive.
    # Do not penalize pairs with too few observations or recent activity.
    def stagnant(row):
        mint,pair,_=row
        s=stats.get((mint,pair))
        if not s or s["total"]<10:return False
        vol=s["last_volume"]
        return s["unknown"]>=10 and s["known"]==0 and (vol is None or vol<250)
    # Preserve half of original shortlist to avoid starving live follow-ups.
    reserve=min(len(legacy),(limit+1)//2)
    kept=legacy[:reserve]
    others=[r for r in expanded if r not in kept]
    # Stable ordering: prefer previously ranked eligible candidates, but
    # move only repeatedly unknown/inactive pairs behind other options.
    ranked=sorted(enumerate(others),key=lambda x:(stagnant(x[1]),x[0]))
    proposed=(kept+[row for _,row in ranked])[:limit]
    return {"legacy":legacy,"proposed":proposed,
            "diagnostics":{"candidate_pool":len(expanded),
                           "baseline_reserved":reserve,
                           "legacy_stagnant_unknown":sum(map(stagnant,legacy)),
                           "proposed_stagnant_unknown":sum(map(stagnant,proposed)),
                           "changed_slots":len(set((x[0],x[1]) for x in proposed)-
                                               set((x[0],x[1]) for x in legacy)),
                           "unobserved_proposed_pairs":sum((x[0],x[1]) not in stats for x in proposed),
                           "shadow_only":True,
                           "no_claim_of_improved_quote_coverage":True}}

def selection_preview(original, pilot, now=None):
    now=now or datetime.now(timezone.utc)
    with sqlite3.connect(Path(original).resolve().as_uri()+"?mode=ro",uri=True) as source:
        with sqlite3.connect(Path(pilot).resolve().as_uri()+"?mode=ro",uri=True) as history:
            result=adaptive_candidate_preview(source,history,now)
    result["legacy"]=[{"mint":m,"pair":p,"snapshot_utc":t} for m,p,t in result["legacy"]]
    result["proposed"]=[{"mint":m,"pair":p,"snapshot_utc":t} for m,p,t in result["proposed"]]
    return result

def fetch(session, mints):
    if not mints:
        return []
    response = session.get(API + ",".join(mints), timeout=20)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected Dexscreener response")
    return data

def extract(pairs, selected):
    """Keep exact source pairs. Missing pair is UNKNOWN rather than a zero."""
    wanted = {mint: pair for mint,pair,_ in selected}
    result = {}
    for p in pairs:
        if not isinstance(p, dict) or p.get("chainId") != "solana":
            continue
        mint = (p.get("baseToken") or {}).get("address")
        pair = p.get("pairAddress")
        if wanted.get(mint) != pair:
            continue
        current = result.get(mint)
        if current is not None:
            continue
        tx = (p.get("txns") or {}).get("m5") or {}
        created = finite(p.get("pairCreatedAt"))
        result[mint] = {
            "pair":pair,
            "price":finite(p.get("priceUsd")),
            "liquidity":finite((p.get("liquidity") or {}).get("usd")),
            "volume":finite((p.get("volume") or {}).get("m5")),
            "buys":finite(tx.get("buys")),
            "sells":finite(tx.get("sells")),
            "created_ms":created,
        }
    return result

def initialize(db):
    db.execute("""
      CREATE TABLE IF NOT EXISTS minute_observations (
        observed_at TEXT NOT NULL, mint TEXT NOT NULL, pair TEXT NOT NULL,
        source_snapshot_at TEXT NOT NULL, status TEXT NOT NULL,
        price_usd REAL, liquidity_usd REAL, volume_m5 REAL,
        buys_m5 REAL, sells_m5 REAL, pair_age_hours REAL,
        PRIMARY KEY (observed_at,mint,pair)
      )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_minute_mint ON minute_observations(mint,observed_at)")

def record(db, stamp, selected, found):
    saved=0
    for mint,pair,source_stamp in selected:
        result=found.get(mint)
        values=None
        if result:
            created=result["created_ms"]
            age=max(0,(datetime.fromisoformat(stamp).timestamp()*1000-created)/3600000) if created else None
            values=(result["price"],result["liquidity"],result["volume"],
                    result["buys"],result["sells"],age)
        # Fail closed. Preserve partial market fields for diagnosis, but
        # never confuse missing liquidity with measured low liquidity.
        if not values or values[0] is None or values[0] <= 0:
            status = "MARKET_DATA_UNAVAILABLE"
        elif values[1] is None:
            status = "LIQUIDITY_UNKNOWN"
        else:
            status = "QUOTE_MARKET_OBSERVED"
        db.execute("""
          INSERT OR IGNORE INTO minute_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,(stamp,mint,pair,source_stamp,status,*(values or (None,)*6)))
        saved+=1
    return saved

def report(db):
    count,mints,last = db.execute("""
      SELECT COUNT(*),COUNT(DISTINCT mint),MAX(observed_at) FROM minute_observations
    """).fetchone()
    last_cycle = db.execute("""
      SELECT status,COUNT(*) FROM minute_observations
      WHERE observed_at=? GROUP BY status
    """,(last,)).fetchall() if last else []
    return {"samples":count,"unique_tokens":mints,"last_cycle_utc":last,
            "last_cycle_statuses":dict(last_cycle),
            "status_note":"LIQUIDITY_UNKNOWN is not zero liquidity and is not entry approval"}

def run(original, pilot, session, now=None):
    now=now or datetime.now(timezone.utc)
    stamp=now.isoformat(timespec="seconds")
    with sqlite3.connect(Path(original).resolve().as_uri()+"?mode=ro",uri=True) as source:
        selected=source_candidates(source,now)
        pairs=fetch(session,[mint for mint,_,_ in selected])
        found=extract(pairs,selected)
        target=Path(pilot)
        target.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(target) as db:
            initialize(db)
            record(db,stamp,selected,found)
            result=report(db)
        # Shadow A/B only: primary pilot observations were already saved.
        # Never change the real selection or fail this service because shadow
        # monitoring had an API/database issue.
        try:
            from research_selection_shadow import compare_cycle
            shadow=compare_cycle(source,pilot,session,now,selected,found)
        except Exception as exc:
            shadow={"status":"ERROR","error":type(exc).__name__+": "+str(exc)[:150]}
    return {"mode":"PAPER_ONLY_MINUTE_MARKET_PILOT",
            "selected":len(selected),"observed":sum(1 for x in selected if x[0] in found),
            "source":"original_read_only_learning_db",
            "storage":"isolated_pilot_sqlite","no_orders":True,
            "selection_shadow":shadow,**result}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--original",default=ORIGINAL)
    p.add_argument("--pilot",default=PILOT)
    p.add_argument("--report-only",action="store_true")
    p.add_argument("--selection-preview",action="store_true",help="Read-only candidate comparison; does not change active selection")
    args=p.parse_args()
    if args.selection_preview:
        print(json.dumps(selection_preview(args.original,args.pilot),indent=2));return
    if args.report_only:
        with sqlite3.connect(Path(args.pilot).resolve().as_uri()+"?mode=ro",uri=True) as db:
            print(json.dumps(report(db)));return
    with requests.Session() as session:
        result=run(args.original,args.pilot,session)
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
