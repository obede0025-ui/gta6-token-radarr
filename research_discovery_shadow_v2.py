"""Discovery Shadow V2: independent DexScreener profile/boost discovery.

A manual, prospective, paper-only research experiment. Reads the original
learning.db read-only to form a contemporaneous 30-token reference, fetches
new candidates from *separate* latest-token-profile and latest-boost lists,
and stores outcomes only in an isolated SQLite file.

Profiles/boosts are visibility/promotion feeds, NOT an unbiased feed of all
Solana launches. There is no order entry, Jupiter call or production approval.
"""
import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import requests

from research_minute_market_pilot import ORIGINAL, API, finite, source_candidates

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SHADOW=ROOT/"discovery_shadow_v2.sqlite"
PROFILE_URL="https://api.dexscreener.com/token-profiles/latest/v1"
BOOST_URL="https://api.dexscreener.com/token-boosts/latest/v1"
MAX_DISCOVERED=60
BASELINE_LIMIT=30
MIN_LIQUIDITY=20000
MIN_VOLUME=250
MIN_GAP=45
MAX_GAP=600
MOMENTUM_RATIO=1.10
MAX_PAIR_AGE_HOURS=24

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS v2_samples (
      observed_at TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,
      cohort TEXT NOT NULL,feed TEXT NOT NULL,price_usd REAL,
      liquidity_usd REAL,volume_m5 REAL,buys_m5 REAL,sells_m5 REAL,
      pair_age_hours REAL,status TEXT NOT NULL,base_pass INTEGER NOT NULL,
      momentum_pass INTEGER NOT NULL,momentum_reason TEXT NOT NULL,
      PRIMARY KEY(observed_at,mint,pair,cohort))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_v2_previous
      ON v2_samples(mint,pair,cohort,observed_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS v2_signals (
      cohort TEXT NOT NULL,mint TEXT NOT NULL,pair TEXT NOT NULL,
      first_signal_utc TEXT NOT NULL,PRIMARY KEY(cohort,mint,pair))""")
    db.execute("""CREATE TABLE IF NOT EXISTS v2_cycles (
      observed_at TEXT PRIMARY KEY,baseline_selected INTEGER,
      discovered_selected INTEGER,baseline_base INTEGER,discovered_base INTEGER,
      baseline_momentum INTEGER,discovered_momentum INTEGER,
      newly_unique_discovery_signals INTEGER,discovered_not_in_baseline INTEGER)""")

def fetch_list(session,url):
    response=session.get(url,timeout=20)
    response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,list):
        raise ValueError("Unexpected discovery-list response")
    return payload

def discover(profile_items,boost_items,limit=MAX_DISCOVERED):
    """Do not infer launch time from list presence. No paid-feed rank claims."""
    items={}
    for label,rows in (("PROFILE",profile_items),("BOOST",boost_items)):
        for row in rows:
            if not isinstance(row,dict) or row.get("chainId")!="solana":
                continue
            mint=row.get("tokenAddress")
            if not isinstance(mint,str) or not (32<=len(mint)<=44):
                continue
            if mint in items:
                items[mint].add(label)
            elif len(items)<limit:
                items[mint]={label}
    return {mint:"+".join(sorted(tags)) for mint,tags in items.items()}

def fetch_markets(session,mints):
    """Batch Solana token GETs, each containing at most 30 addresses."""
    payload=[]
    for start in range(0,len(mints),30):
        res=session.get(API+",".join(mints[start:start+30]),timeout=20)
        res.raise_for_status()
        data=res.json()
        if not isinstance(data,list):
            raise ValueError("Unexpected token response")
        payload.extend(data)
    return payload

def choose_pairs(payload,wanted):
    """Select a *real* exact Solana base-token pair, favoring known liquidity.

    Selection is descriptive only; switching pair across cycles does not
    carry forward momentum for the previous pair.
    """
    best={}
    for p in payload:
        if not isinstance(p,dict) or p.get("chainId")!="solana":
            continue
        mint=(p.get("baseToken") or {}).get("address")
        pair=p.get("pairAddress")
        if mint not in wanted or not isinstance(pair,str) or not pair:
            continue
        liq=finite((p.get("liquidity") or {}).get("usd"))
        price=finite(p.get("priceUsd"))
        if liq is not None and liq<0:liq=None
        rank=(int(price is not None and price>0 and liq is not None),
              liq if liq is not None else -1,
              str(pair))
        if mint not in best or rank>best[mint][0]:
            best[mint]=(rank,p)
    return {mint:item[1] for mint,item in best.items()}

def prior(db,mint,pair,cohort,stamp):
    return db.execute("""SELECT observed_at,volume_m5
        FROM v2_samples WHERE mint=? AND pair=? AND cohort=?
        AND observed_at<? ORDER BY observed_at DESC LIMIT 1""",
        (mint,pair,cohort,stamp)).fetchone()

def momentum(now,old,volume,buys,sells):
    if old is None:return False,"AWAITING_SECOND_SAMPLE"
    try:gap=(now-datetime.fromisoformat(old[0])).total_seconds()
    except (TypeError,ValueError):return False,"INVALID_PREVIOUS_TIME"
    if gap<MIN_GAP:return False,"TOO_SOON"
    if gap>MAX_GAP:return False,"PRIOR_TOO_OLD"
    if volume is None or old[1] is None or old[1]<=0:
        return False,"VOLUME_UNAVAILABLE"
    if buys is None or sells is None or buys<=sells:
        return False,"BUY_PRESSURE_UNCONFIRMED"
    if volume<old[1]*MOMENTUM_RATIO:
        return False,"NO_VOLUME_GROWTH"
    return True,"SPACED_VOLUME_GROWTH"

def make_sample(db,now,mint,pair,cohort,feed,market):
    stamp=now.isoformat(timespec="seconds")
    price=finite(market.get("priceUsd")) if market else None
    liq=finite((market.get("liquidity") or {}).get("usd")) if market else None
    if liq is not None and liq<0:liq=None
    volume=finite((market.get("volume") or {}).get("m5")) if market else None
    tx=(market.get("txns") or {}).get("m5") or {} if market else {}
    buys=finite(tx.get("buys"))
    sells=finite(tx.get("sells"))
    created=finite(market.get("pairCreatedAt")) if market else None
    age=(now.timestamp()*1000-created)/3600000 if created is not None and created>0 else None
    if age is not None and (age<0 or not math.isfinite(age)):age=None
    status=("MARKET_DATA_UNAVAILABLE" if price is None or price<=0 else
            "LIQUIDITY_UNKNOWN" if liq is None else
            "QUOTE_MARKET_OBSERVED")
    # Age gate is included in V2; no unknown age receives approval.
    base=(status=="QUOTE_MARKET_OBSERVED" and
          liq>=MIN_LIQUIDITY and volume is not None and volume>=MIN_VOLUME
          and buys is not None and sells is not None and buys>sells
          and age is not None and 0<=age<=MAX_PAIR_AGE_HOURS)
    is_momentum,reason=momentum(now,prior(db,mint,pair,cohort,stamp),
                                volume,buys,sells)
    return (stamp,mint,pair,cohort,feed,price,liq,volume,buys,sells,age,
            status,int(base),int(base and is_momentum),reason)

def count(rows):
    return {"selected":len(rows),
            "known_liquidity":sum(r[11]=="QUOTE_MARKET_OBSERVED" for r in rows),
            "unknown_liquidity":sum(r[11]=="LIQUIDITY_UNKNOWN" for r in rows),
            "base_pass_including_pair_age":sum(r[12] for r in rows),
            "spaced_momentum_pass":sum(r[13] for r in rows),
            "new_unique_mints_with_base_pass":len({r[1] for r in rows if r[12]})}

def run(original=ORIGINAL,shadow=SHADOW,now=None,session=None,
        max_discovered=MAX_DISCOVERED):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 1<=max_discovered<=MAX_DISCOVERED:
        raise ValueError("aware timestamp and max_discovered 1..60 required")
    with sqlite3.connect(Path(original).resolve().as_uri()+"?mode=ro",uri=True) as source:
        source.execute("PRAGMA query_only=ON")
        reference=source_candidates(source,now,limit=BASELINE_LIMIT)
    baseline={mint:pair for mint,pair,_ in reference}
    def collect(client):
        profiles=fetch_list(client,PROFILE_URL)
        boosts=fetch_list(client,BOOST_URL)
        discovery=discover(profiles,boosts,limit=max_discovered)
        all_mints=list(dict.fromkeys([*baseline,*discovery]))
        markets=fetch_markets(client,all_mints)
        # Baseline must use original pair ID (not a replacement). New feed
        # tokens get best *actual* pair from the same API response.
        matched_baseline={}
        for item in markets:
            if not isinstance(item,dict) or item.get("chainId")!="solana":continue
            mint=(item.get("baseToken") or {}).get("address")
            if mint in baseline and item.get("pairAddress")==baseline[mint]:
                matched_baseline[mint]=item
        selected=choose_pairs(markets,set(discovery))
        target=Path(shadow)
        target.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(target) as db:
            initialize(db)
            stamp=now.isoformat(timespec="seconds")
            # Do not issue HTTP before checking duplicate-second for idempotency
            # of local inserts; samples remain unchanged if same timestamp.
            a=[make_sample(db,now,m,p,"BASELINE","LEARNING_DB",
                           matched_baseline.get(m)) for m,p in baseline.items()]
            b=[make_sample(db,now,m,selected[m]["pairAddress"],"DISCOVERY",
                           discovery[m],selected[m]) for m in discovery if m in selected]
            for row in a+b:
                db.execute("INSERT OR IGNORE INTO v2_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
            new=0
            for row in b:
                if not row[13]:continue
                prior_changes=db.total_changes
                db.execute("INSERT OR IGNORE INTO v2_signals VALUES(?,?,?,?)",
                           ("DISCOVERY",row[1],row[2],stamp))
                new+=int(db.total_changes>prior_changes)
            # Baseline signal identities are tracked separately, too.
            for row in a:
                if row[13]:
                    db.execute("INSERT OR IGNORE INTO v2_signals VALUES(?,?,?,?)",
                               ("BASELINE",row[1],row[2],stamp))
            seen_new=len(set(discovery)-set(baseline))
            summary_a=count(a);summary_b=count(b)
            db.execute("INSERT OR IGNORE INTO v2_cycles VALUES(?,?,?,?,?,?,?,?,?)",(
                stamp,summary_a["selected"],summary_b["selected"],
                summary_a["base_pass_including_pair_age"],
                summary_b["base_pass_including_pair_age"],
                summary_a["spaced_momentum_pass"],summary_b["spaced_momentum_pass"],
                new,seen_new))
            return {"mode":"DISCOVERY_V2_INDEPENDENT_FEED_SHADOW",
                    "observed_utc":stamp,
                    "baseline":summary_a,"independent_feed":summary_b,
                    "discovery_mints_not_in_baseline":seen_new,
                    "feed_candidates_without_current_pair":len(discovery)-len(b),
                    "new_unique_discovery_signals":new,
                    "calls":{"latest_profiles":1,"latest_boosts":1,
                             "token_batches":(len(all_mints)+29)//30},
                    "paper_only":True,"no_original_db_writes":True,
                    "caveats":[
                      "Profiles and boosts are discovery/promotion feeds, NOT an unbiased new-launch firehose.",
                      "PairCreatedAt measures pool age, NOT mint creation time.",
                      "The spaced rolling 5m volume proxy is NOT the full production B3 momentum filter.",
                      "Unknown liquidity, age and prices fail closed.",
                      "No Jupiter quotes, fills, launch-latency claims or trading approvals."]}
    if session is not None:return collect(session)
    with requests.Session() as client:return collect(client)

def report(shadow=SHADOW):
    path=Path(shadow)
    if not path.is_file():
        return {"mode":"DISCOVERY_V2_REPORT","status":"NO_SAMPLES"}
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        cycle=db.execute("""SELECT COUNT(*),COALESCE(SUM(baseline_base),0),
         COALESCE(SUM(discovered_base),0),
         COALESCE(SUM(baseline_momentum),0),COALESCE(SUM(discovered_momentum),0),
         COALESCE(SUM(newly_unique_discovery_signals),0)
         FROM v2_cycles""").fetchone()
        signals=db.execute("""SELECT cohort,COUNT(*) FROM v2_signals
            GROUP BY cohort""").fetchall()
    return {"mode":"DISCOVERY_V2_REPORT","cycles":cycle[0],
            "baseline_base_observations":cycle[1],
            "discovery_base_observations":cycle[2],
            "baseline_momentum_observations":cycle[3],
            "discovery_momentum_observations":cycle[4],
            "new_discovery_signal_events":cycle[5],
            "unique_signals_by_cohort":dict(signals),
            "research_only":True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original",default=ORIGINAL)
    parser.add_argument("--shadow",default=str(SHADOW))
    parser.add_argument("--max-discovered",type=int,default=60)
    parser.add_argument("--report-only",action="store_true")
    args=parser.parse_args()
    out=report(args.shadow) if args.report_only else run(
        args.original,args.shadow,max_discovered=args.max_discovered)
    print(json.dumps(out,indent=2))
if __name__=="__main__":
    main()
