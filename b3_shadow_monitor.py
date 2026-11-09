"""B3 forward-only research recorder. No real/paper trades, wallet access or network."""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from b3_candidate_audit import (
    MIN_LIQ_USD, MIN_VOLUME_M5_USD, MAX_PAIR_AGE_HOURS, parsed,
)
from v611_b2_shadow import signal_b

MAX_AGE_SECONDS = 900
MAX_EVENTS = 10000

def latest_cursor(conn):
    row = conn.execute("""SELECT scanned_at,token_address,pair_address FROM snapshots
        ORDER BY scanned_at DESC,token_address DESC,pair_address DESC LIMIT 1""").fetchone()
    if not row:
        raise ValueError("No snapshots in learning database")
    return list(row)

def evaluate(conn, row):
    stamp, mint, pair, name, price, liquidity, volume, buys, sells, age = row
    reasons = []
    if price is None or price <= 0: reasons.append("INVALID_PRICE")
    if liquidity is None or liquidity < MIN_LIQ_USD: reasons.append("LIQUIDITY_BELOW_20000")
    if volume is None or volume < MIN_VOLUME_M5_USD: reasons.append("VOLUME_BELOW_250")
    if age is None or not 0 <= age <= MAX_PAIR_AGE_HOURS: reasons.append("PAIR_AGE_INVALID")
    if buys is None or sells is None or buys <= sells: reasons.append("BUY_SELL_IMBALANCE")
    if not reasons and not signal_b(conn,stamp,mint,pair,price,volume):
        reasons.append("NO_B_MOMENTUM_SIGNAL")
    return {"scanned_at":stamp,"mint":mint,"pair":pair,"name":name,
            "price_usd":price,"liquidity_usd":liquidity,"volume_m5_usd":volume,
            "decision":"REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW" if not reasons else "BLOCKED",
            "reasons":reasons}

def run(db, state_path, now=None):
    now = now or datetime.now(timezone.utc)
    db = Path(db)
    state_path = Path(state_path)
    if not db.is_file(): raise FileNotFoundError("Existing database not found")
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite database failed quick_check")
        if state_path.exists():
            state=json.loads(state_path.read_text(encoding="utf-8"))
            if (state.get("version") != 1 or not isinstance(state.get("cursor"),list)
                    or len(state["cursor"]) != 3 or not isinstance(state.get("events"),list)):
                raise ValueError("Invalid B3 state: refusing to overwrite")
        else:
            state={"version":1,"cursor":latest_cursor(conn),"started_utc":now.isoformat(),
                   "events":[],"total_logged":0}
            state_path.parent.mkdir(parents=True,exist_ok=True)
            # x mode prevents silently overwriting existing state
            with state_path.open("x",encoding="utf-8") as stream:
                json.dump(state,stream,ensure_ascii=False,indent=2)
                stream.write("\n")
            return {"mode":"B3_FORWARD_ONLY_RESEARCH","bootstrapped":True,"new":0,
                    "note":"Only future snapshots will be assessed. No historical replay."}
        rows=conn.execute("""SELECT scanned_at,token_address,pair_address,name,price_usd,
            liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours FROM snapshots
            WHERE scanned_at>=? ORDER BY scanned_at,token_address,pair_address""",
            (state["cursor"][0],)).fetchall()
        fresh=[]
        for row in rows:
            key=list(row[:3])
            if key<=state["cursor"]:continue
            delta=(now-parsed(row[0])).total_seconds()
            if delta<0 or delta>MAX_AGE_SECONDS:
                state["cursor"]=key
                continue
            candidate=evaluate(conn,row)
            candidate["recorded_utc"]=datetime.now(timezone.utc).isoformat()
            fresh.append(candidate)
            state["cursor"]=key
    state["events"]=(state["events"]+fresh)[-MAX_EVENTS:]
    state["total_logged"]+=len(fresh)
    state["updated_utc"]=now.isoformat()
    temp=state_path.with_suffix(state_path.suffix+".tmp")
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(state_path)
    counts=Counter(event["decision"] for event in fresh)
    return {"mode":"B3_FORWARD_ONLY_RESEARCH","bootstrapped":False,
            "new":len(fresh),"decisions":dict(counts),
            "total_logged":state["total_logged"],"last_cursor":state["cursor"],
            "note":"Signals logged only; no simulated or live orders."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--state",required=True)
    args=p.parse_args()
    print(json.dumps(run(Path(args.db),Path(args.state)),ensure_ascii=False))
if __name__=="__main__":main()
