"""B3 experimental candidate audit, read-only; no trades and no replayed PnL."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from v611_b2_shadow import signal_b

MIN_LIQ_USD = 20000.0
MIN_VOLUME_M5_USD = 250.0
MAX_PAIR_AGE_HOURS = 24.0
MAX_OBSERVATION_AGE_SECONDS = 150

def parsed(stamp):
    d = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Naive timestamps not allowed")
    return d.astimezone(timezone.utc)

def examine(db_path, now=None):
    now = now or datetime.now(timezone.utc)
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError("Market DB absent")
    output = {"mode":"B3_CANDIDATE_AUDIT_READ_ONLY",
              "evaluated_utc":now.isoformat(timespec="seconds"),
              "status":"RESEARCH_ONLY_NO_TRADES",
              "candidates":[], "counts":{"recent":0,"eligible":0,"blocked":0}}
    with sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0]!="ok":
            raise ValueError("DB integrity error")
        rows=conn.execute("""
            SELECT scanned_at,token_address,pair_address,name,price_usd,
                   liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
            FROM snapshots
            ORDER BY scanned_at DESC,token_address DESC,pair_address DESC
            LIMIT 500
        """).fetchall()
        seen=set()
        for stamp,mint,pair,name,price,liq,vol,buys,sells,age in rows:
            try:
                delta=(now-parsed(stamp)).total_seconds()
            except (ValueError,TypeError):
                continue
            if delta < 0 or delta > MAX_OBSERVATION_AGE_SECONDS:
                continue
            if mint in seen:
                continue
            seen.add(mint)
            output["counts"]["recent"]+=1
            reasons=[]
            if price is None or price<=0: reasons.append("INVALID_PRICE")
            if liq is None or liq < MIN_LIQ_USD: reasons.append("LIQUIDITY_BELOW_20000")
            if vol is None or vol < MIN_VOLUME_M5_USD: reasons.append("VOLUME_BELOW_250")
            if age is None or not 0<=age<=MAX_PAIR_AGE_HOURS: reasons.append("PAIR_AGE_INVALID")
            if buys is None or sells is None or buys<=sells: reasons.append("BUY_SELL_IMBALANCE")
            if not reasons and not signal_b(conn,stamp,mint,pair,price,vol):
                reasons.append("NO_B_MOMENTUM_SIGNAL")
            status="CANDIDATE_REQUIRES_QUOTE_AND_TOKEN_AUDIT" if not reasons else "BLOCKED"
            output["counts"]["eligible" if not reasons else "blocked"]+=1
            output["candidates"].append({
                "mint":mint,"pair":pair,"name":name,"scanned_at":stamp,
                "liquidity_usd":liq,"volume_m5_usd":vol,
                "status":status,"reasons":reasons})
    output["warning"]="Research-only snapshot filter. Not live trading approval or forward performance evaluation."
    return output

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    result=examine(args.db)
    destination=Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name(destination.name+".tmp")
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(destination)
    print(json.dumps(result["counts"]))

if __name__=="__main__":
    main()
