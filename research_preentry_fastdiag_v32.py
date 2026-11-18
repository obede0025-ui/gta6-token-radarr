"""Fast read-only GTA6 preflight: explain zero candidates without external API calls.

Uses a bounded recent scan window, indexed snapshot count and same candidate
selector as V3.1. Does not alter thresholds, positions, timers or DB.
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_preentry_probe_v31 import selection

DEFAULT_DB="/home/ubuntu/gta6-paper-vps/data/learning.db"
DEFAULT_STATE="/home/ubuntu/gta6-paper-vps/data/v591/portfolio.json"

def diagnose(db_path,state_path,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("timezone-aware now required")
    db=Path(db_path);state=Path(state_path)
    result={"mode":"GTA6_V32_FAST_PREENTRY_DIAG",
        "checked_utc":now.isoformat(timespec="seconds"),
        "database_present":db.is_file(),"state_present":state.is_file(),
        "diagnosis":[],"no_trades":True,"database_writes":0,
        "portfolio_writes":0}
    if not db.is_file() or not state.is_file():
        result["diagnosis"]=["MISSING_DATABASE_OR_STATE"];return result
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        latest=con.execute("SELECT MAX(scanned_at) FROM snapshots").fetchone()[0]
        result["latest_snapshot_utc"]=latest
        if latest:
            try:
                t=datetime.fromisoformat(str(latest).replace("Z","+00:00"))
                if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
                result["latest_age_minutes"]=round((now-t).total_seconds()/60,2)
            except (ValueError,TypeError,OverflowError):
                result["latest_age_minutes"]=None
        else:result["latest_age_minutes"]=None
        cutoff=(now-timedelta(minutes=12)).isoformat(timespec="seconds")
        totals=con.execute("""SELECT COUNT(*),
          COUNT(DISTINCT token_address || ':' || pair_address),
          SUM(CASE WHEN liquidity_usd>=20000 THEN 1 ELSE 0 END),
          SUM(CASE WHEN liquidity_usd>=20000 AND volume_m5>=250 THEN 1 ELSE 0 END),
          SUM(CASE WHEN liquidity_usd>=20000 AND volume_m5>=250 AND buys_m5>sells_m5 THEN 1 ELSE 0 END)
          FROM snapshots WHERE scanned_at>=?""",(cutoff,)).fetchone()
        result["last_12m"]={"snapshots":totals[0] or 0,
          "distinct_pairs":totals[1] or 0,
          "liquidity_ge_20k":totals[2] or 0,
          "liquidity_and_volume_ge_250":totals[3] or 0,
          "liquidity_volume_buy_pressure":totals[4] or 0}
        result["snapshot_indexes"]=[r[1] for r in con.execute("PRAGMA index_list('snapshots')").fetchall()]
    stat=json.loads(state.read_text(encoding="utf-8"))
    if stat.get("version")!=1 or not isinstance(stat.get("open"),dict):
        result["diagnosis"].append("INVALID_STATE")
        return result
    result["open_by_strategy"]={k:len(stat["open"].get(k) or {}) for k in ("A","B","C")}
    # selection is identical to V3.1; it performs no API requests.
    try:
        candidates=selection(db,state,now,5)
        result["selected_candidates"]=len(candidates)
    except Exception as exc:
        result["selected_candidates"]=None
        result["diagnosis"].append("SELECTION_ERROR_"+type(exc).__name__)
        return result
    age=result["latest_age_minutes"]
    if age is None or age<0 or age>12:result["diagnosis"].append("SCANNER_DATA_STALE")
    if result["last_12m"]["snapshots"]==0:result["diagnosis"].append("NO_RECENT_SNAPSHOTS")
    elif result["last_12m"]["liquidity_volume_buy_pressure"]==0:
        result["diagnosis"].append("MARKET_FILTERS_REJECT_RECENT_DATA")
    elif not candidates:result["diagnosis"].append("MOMENTUM_OR_PORTFOLIO_FILTERS_REJECT_ALL")
    if not result["diagnosis"]:result["diagnosis"].append("RECENT_CANDIDATES_PRESENT")
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=DEFAULT_DB)
    p.add_argument("--state",default=DEFAULT_STATE)
    a=p.parse_args()
    print(json.dumps(diagnose(a.db,a.state),indent=2,ensure_ascii=False))
if __name__=="__main__":main()
