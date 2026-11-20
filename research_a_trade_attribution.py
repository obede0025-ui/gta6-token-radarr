"""Read-only historical attribution for all closed Strategy A paper positions.

No retrospective Jupiter quote reconstruction or executed-fill claims.
Only mint/pair-matched snapshots at or before entry are eligible features.
"""
import argparse
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

STAKE = 25.0

def utc(raw):
    if not isinstance(raw, str): return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, OverflowError): return None

def num(raw):
    if raw is None or isinstance(raw, bool): return None
    try:
        x = float(raw)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError): return None

def snapshot(conn, mint, pair, at):
    return conn.execute("""SELECT scanned_at,price_usd,liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
        FROM snapshots WHERE token_address=? AND pair_address=?
        AND julianday(scanned_at)<=julianday(?)
        ORDER BY julianday(scanned_at) DESC LIMIT 1""", (mint, pair, at.isoformat())).fetchone()

def summarize(rows):
    vals = [r["paper_pnl_usd"] for r in rows]
    if not vals: return {"count": 0, "pnl_usd": 0, "wins": 0}
    return {"count":len(vals), "pnl_usd": round(sum(vals),5),
        "wins":sum(v>0 for v in vals), "win_rate_pct":round(100*sum(v>0 for v in vals)/len(vals),2),
        "mean_pnl_usd":round(mean(vals),5), "median_pnl_usd":round(median(vals),5),
        "median_minutes_to_exit":round(median(r["duration_minutes"] for r in rows),2)}

def analyze(db, portfolio):
    state = json.loads(Path(portfolio).read_text(encoding="utf-8"))
    closed = (state.get("closed") or {}).get("A")
    opened = (state.get("open") or {}).get("A")
    if not isinstance(closed, list) or not isinstance(opened, dict):
        raise ValueError("Expected A strategy portfolio")
    rows=[]
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        for i,t in enumerate(closed,1):
            if not isinstance(t,dict): raise ValueError("Malformed trade")
            mint,pair=t.get("token"),t.get("pair")
            start,end=utc(t.get("opened_at")),utc(t.get("closed_at"))
            pnl=num(t.get("pnl_usd"))
            if not mint or not pair or start is None or end is None or end<start or pnl is None:
                raise ValueError("Incomplete trade record")
            entry=snapshot(conn,mint,pair,start)
            last=snapshot(conn,mint,pair,end)
            entry_stamp=utc(entry[0]) if entry else None
            exit_stamp=utc(last[0]) if last else None
            entry_gap=(start-entry_stamp).total_seconds() if entry_stamp else None
            exit_gap=(end-exit_stamp).total_seconds() if exit_stamp else None
            entry_ok=entry_gap is not None and 0<=entry_gap<=120
            # Require <=1 sec for historically exact exit observation.
            exit_ok=exit_gap is not None and 0<=exit_gap<=1
            buys=num(entry[4]) if entry_ok else None
            sells=num(entry[5]) if entry_ok else None
            buy_share=buys/(buys+sells) if buys is not None and sells is not None and buys>=0 and sells>=0 and buys+sells>0 else None
            rows.append({
                "index":i,"mint":mint,"pair":pair,"name":t.get("name"),
                "opened_utc":start.isoformat(),"closed_utc":end.isoformat(),
                "exit_reason":t.get("reason"),
                "duration_minutes":round((end-start).total_seconds()/60,3),
                "paper_pnl_usd":round(pnl,5),
                "paper_return_pct":round(pnl/STAKE*100,3),
                "entry_evidence":"FRESH" if entry_ok else "MISSING_OR_STALE",
                "entry_snapshot_gap_seconds":entry_gap,
                "entry_liquidity_usd":num(entry[2]) if entry_ok else None,
                "entry_volume_5m":num(entry[3]) if entry_ok else None,
                "entry_buy_share":round(buy_share,4) if buy_share is not None else None,
                "entry_pair_age_hours":num(entry[6]) if entry_ok else None,
                "exit_mark_evidence":"MATCHED_WITHIN_1S" if exit_ok else "MISSING_OR_STALE",
                "exit_liquidity_usd":num(last[2]) if exit_ok else None,
                "historical_sell_quote":"UNKNOWN_NOT_IN_SOURCE",
                "executed_trade":False,
            })
    winners=[r for r in rows if r["paper_pnl_usd"]>0]
    losers=[r for r in rows if r["paper_pnl_usd"]<=0]
    total=sum(r["paper_pnl_usd"] for r in rows)
    positive=sum(max(0,r["paper_pnl_usd"]) for r in rows)
    top3=sum(r["paper_pnl_usd"] for r in sorted(winners,key=lambda r:r["paper_pnl_usd"],reverse=True)[:3])
    groups={}
    for label,subset in (("winning",winners),("non_winning",losers)):
        groups[label]={"summary":summarize(subset),
            "entry_liquidity_usd":{"median":round(median([r["entry_liquidity_usd"] for r in subset if r["entry_liquidity_usd"] is not None]),2) if any(r["entry_liquidity_usd"] is not None for r in subset) else None},
            "entry_volume_5m":{"median":round(median([r["entry_volume_5m"] for r in subset if r["entry_volume_5m"] is not None]),2) if any(r["entry_volume_5m"] is not None for r in subset) else None},
            "entry_buy_share":{"median":round(median([r["entry_buy_share"] for r in subset if r["entry_buy_share"] is not None]),4) if any(r["entry_buy_share"] is not None for r in subset) else None}}
    return {
        "mode":"A_CLOSED_TRADE_ATTRIBUTION_READ_ONLY",
        "closed":summarize(rows),
        "open_count":len(opened),
        "total_loss_on_open_stress_pnl_usd":round(total-len(opened)*STAKE,5),
        "top_3_winning_trades_contribution_usd":round(top3,5),
        "top_3_as_pct_of_positive_pnl":round(100*top3/positive,2) if positive else None,
        "by_exit_reason":{str(k):summarize([r for r in rows if r["exit_reason"]==k]) for k in sorted({r["exit_reason"] for r in rows},key=str)},
        "entry_evidence_coverage":{"fresh":sum(r["entry_evidence"]=="FRESH" for r in rows),"missing_or_stale":sum(r["entry_evidence"]!="FRESH" for r in rows)},
        "exit_mark_coverage":{"matched":sum(r["exit_mark_evidence"]=="MATCHED_WITHIN_1S" for r in rows)},
        "groups":groups,
        "trades":rows,
        "source_db_writes":0,"portfolio_writes":0,"trades_executed":0,
        "limitations":["No historical Jupiter sell quotes for closed A positions in this source; no proof of fills.",
            "Price-only paper P/L may overstate or understate realized trading performance.",
            "Closed trades are selected and open positions remain exposed; total-loss scenario is illustrative.",
            "Snapshot features only at or before entry; no validation that engine used identical snapshot.",
            "Observed irregularities are not predictive until evaluated prospectively out-of-sample."]
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--portfolio",required=True)
    args=p.parse_args()
    print(json.dumps(analyze(args.db,args.portfolio),indent=2))
if __name__=="__main__":
    main()
