"""Read-only pre-signal market evidence for Jupiter quote-only candidates.

Use exact mint + pair only, strictly observed <= original M1 signal time.
Never infer liquidity from missing data, and never use later observations.
No networking, database writes, trading or parameter optimization.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_jupiter_minute_quotes import STATE, dt
from research_jupiter_minute_evaluation import evaluate

MINUTE="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
ORIGINAL="/home/ubuntu/gta6-paper-vps/data/learning.db"
MAX_LOOKBACK_SECONDS=600

def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)

def previous(db,table,mint,pair,signal,columns):
    if not mint or not pair or signal is None:return None
    names={r[1] for r in db.execute("PRAGMA table_info("+table+")")}
    required={"mint","pair","observed_at"} if table=="minute_observations" else {
        "token_address","pair_address","scanned_at"}
    if not required<=names:return None
    stamp_col="observed_at" if table=="minute_observations" else "scanned_at"
    mint_col="mint" if table=="minute_observations" else "token_address"
    pair_col="pair" if table=="minute_observations" else "pair_address"
    wanted=[x for x in columns if x in names]
    fields=[stamp_col]+wanted
    row=db.execute("SELECT "+",".join(fields)+" FROM "+table+
                   " WHERE "+mint_col+"=? AND "+pair_col+"=? AND "+
                   stamp_col+"<=? ORDER BY "+stamp_col+" DESC LIMIT 1",
                   (mint,pair,signal.isoformat(timespec="seconds"))).fetchone()
    if not row:return None
    sample_time=dt(row[0])
    if sample_time is None:return None
    age=(signal-sample_time).total_seconds()
    if age<0 or age>MAX_LOOKBACK_SECONDS:return None
    return {"sample_utc":row[0],"lag_seconds":round(age,1),
            **dict(zip(wanted,row[1:]))}

def report(state,minute,original):
    if state.get("mode")!="JUPITER_MINUTE_QUOTE_ONLY":raise ValueError("Invalid Jupiter state")
    cols_min=["status","liquidity_usd","volume_m5","buys_m5","sells_m5","pair_age_hours","price_usd"]
    cols_org=["liquidity_usd","volume_m5","pair_age_hours"]
    rows=[]
    with ro(minute) as md,ro(original) as od:
        for item in state.get("positions",{}).values():
            if not isinstance(item,dict):continue
            mint,pair=item.get("mint"),item.get("pair")
            signal=dt(item.get("signal_utc"))
            q=evaluate(item)
            old=previous(od,"snapshots",mint,pair,signal,cols_org)
            minute_row=previous(md,"minute_observations",mint,pair,signal,cols_min)
            # Prefer minute evidence when available: has explicit market status
            # and buy/sell counts. Do not fill missing fields from other pairs.
            source="minute" if minute_row else "original" if old else None
            pre=minute_row if minute_row else old
            row={"mint":mint,"pair":pair,"signal_utc":item.get("signal_utc"),
                 "gate":q["gate"],"outcome":q["outcome"],
                 "stress_quote_pct":q["stress_quote_pct"],
                 "immediate_roundtrip_pct":q.get("immediate_roundtrip_pct"),
                 "pre_signal_source":source,"pre_signal":pre,
                 "minute_pre_signal":minute_row,"original_pre_signal":old}
            rows.append(row)
    groups={}
    for label,subset in (
        ("passing_tp",[r for r in rows if r["gate"].startswith("PASS_") and r["outcome"]=="TP_QUOTE_OBSERVED"]),
        ("passing_sl",[r for r in rows if r["gate"].startswith("PASS_") and r["outcome"]=="SL_QUOTE_OBSERVED"]),
        ("passing_censored",[r for r in rows if r["gate"].startswith("PASS_") and r["outcome"]=="CENSORED_BEFORE_HORIZON"]),
        ("blocked",[r for r in rows if r["gate"].startswith("BLOCKED_")])):
        known=[r["pre_signal"] for r in subset if r["pre_signal"]]
        def numeric(key):
            vals=[float(s[key]) for s in known if isinstance(s.get(key),(int,float)) and not isinstance(s.get(key),bool)]
            vals.sort()
            return {"n":len(vals),"median":round((vals[(len(vals)-1)//2]+vals[len(vals)//2])/2,4)} if vals else {"n":0,"median":None}
        groups[label]={"candidates":len(subset),"pre_signal_covered":len(known),
                       "liquidity_usd":numeric("liquidity_usd"),
                       "volume_m5":numeric("volume_m5"),
                       "pair_age_hours":numeric("pair_age_hours"),
                       "buy_sell_known":sum(s.get("buys_m5") is not None and
                                             s.get("sells_m5") is not None for s in known)}
    return {"mode":"READ_ONLY_PRE_SIGNAL_RISK_EVIDENCE",
            "lookback_max_seconds":MAX_LOOKBACK_SECONDS,
            "count":len(rows),"coverage":{"matched":sum(r["pre_signal"] is not None for r in rows),
                     "missing":sum(r["pre_signal"] is None for r in rows),
                     "minute":sum(r["pre_signal_source"]=="minute" for r in rows),
                     "original":sum(r["pre_signal_source"]=="original" for r in rows)},
            "groups":groups,
            "passing_sl_details":[r for r in rows if r["gate"].startswith("PASS_") and
                                  r["outcome"]=="SL_QUOTE_OBSERVED"],
            "other_unmatched":[{"mint":r["mint"],"signal_utc":r["signal_utc"]}
                               for r in rows if r["pre_signal"] is None],
            "limitations":[
                "Only mint+exact-pair matching and samples at/before signal time.",
                "A market snapshot may predate the eventual quote-based hypothetical entry.",
                "Original snapshots do not necessarily include buy/sell fields.",
                "Missing pre-signal observations never imply zero liquidity or zero volume.",
                "No pre-entry volatility can be assessed using a single prior sample.",
                "The quote router may not use this exact DexScreener pool.",
                "Historical group differences are not causal or an optimized trading strategy.",
                "No API calls, wallet actions, trading or writes."
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",default=STATE)
    p.add_argument("--minute",default=MINUTE)
    p.add_argument("--original",default=ORIGINAL)
    args=p.parse_args()
    print(json.dumps(report(json.loads(Path(args.state).read_text()),
                            args.minute,args.original),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
