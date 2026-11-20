"""Read-only A-first-minutes audit using *prospective* focus cohort.

No look-ahead: each horizon uses the first observed snapshot at/after its
actual target time, within 60 seconds. Never fills missing measurements,
does not construct historical Jupiter quotes, and does not trade.
"""
import argparse
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

HORIZONS = (60, 180, 300)
MAX_LAG_SECONDS = 60
BASE_MAX_AGE = 120


def utc(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, OverflowError):
        return None


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def buy_share(buys, sells):
    b, s = numeric(buys), numeric(sells)
    if b is None or s is None or b < 0 or s < 0 or b+s <= 0:
        return None
    return round(b/(b+s), 6)


def change_pct(start, end):
    a, b = numeric(start), numeric(end)
    if a is None or b is None or a <= 0:
        return None
    return round(100*(b/a-1), 4)


def observation(conn, mint, pair, start, end, before):
    if before:
        sql = """SELECT scanned_at,price_usd,liquidity_usd,volume_m5,
                       buys_m5,sells_m5 FROM snapshots
                 WHERE token_address=? AND pair_address=?
                   AND julianday(scanned_at)<=julianday(?)
                   AND julianday(scanned_at)>=julianday(?)
                 ORDER BY julianday(scanned_at) DESC LIMIT 1"""
        params = (mint, pair, end.isoformat(), start.isoformat())
    else:
        sql = """SELECT scanned_at,price_usd,liquidity_usd,volume_m5,
                       buys_m5,sells_m5 FROM snapshots
                 WHERE token_address=? AND pair_address=?
                   AND julianday(scanned_at)>=julianday(?)
                   AND julianday(scanned_at)<=julianday(?)
                 ORDER BY julianday(scanned_at) ASC LIMIT 1"""
        params = (mint, pair, start.isoformat(), end.isoformat())
    return conn.execute(sql, params).fetchone()


def quote_info(item, horizon):
    """Quotes are optional evidence, NEVER converted into executed P/L."""
    followups = item.get("followups", {})
    if not isinstance(followups, dict):
        return {"status": "INVALID_COHORT", "quote_return_pct_before_fees": None}
    entry_raw = item.get("initial_buy_raw")
    first_sale_raw = item.get("initial_sell_usdc_raw")
    initial = {
        "status": item.get("entry_quote_status", "UNKNOWN"),
        "immediate_reverse_usdc": round(int(first_sale_raw)/1e6, 6)
            if isinstance(first_sale_raw, str) and first_sale_raw.isdigit() else None
    }
    if horizon == 0:
        return initial
    follow = followups.get(str(horizon))
    if not isinstance(follow, dict):
        return {"status": "NOT_RECORDED", "quote_return_pct_before_fees": None}
    # Quote return assumes research stake 10 USDC; amount is fixed hypothetical token qty.
    raw = follow.get("sell_usdc_raw")
    status = follow.get("status", "UNKNOWN")
    result = {"status": status, "quote_return_pct_before_fees": None}
    if status == "SELL_QUOTE_ONLY" and isinstance(raw, str) and raw.isdigit():
        result["quote_return_pct_before_fees"] = round((int(raw)/1e7-1)*100, 4)
    return result


def evaluate(db_path, cohort_path, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    cohort = json.loads(Path(cohort_path).read_text(encoding="utf-8"))
    if cohort.get("version") != 1 or not isinstance(cohort.get("signals"), dict):
        raise ValueError("Invalid focus cohort")
    output = []
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        for key, item in sorted(cohort["signals"].items()):
            mint, pair = item.get("mint"), item.get("pair")
            signal = utc(item.get("signal_utc"))
            if not mint or not pair or not signal or signal > now:
                continue
            # Baseline is a prior observed market mark, not an implied trade execution.
            base = observation(conn, mint, pair,
                signal-timedelta(seconds=BASE_MAX_AGE), signal, True)
            baseline = None
            if base:
                t = utc(base[0])
                if t and 0 <= (signal-t).total_seconds() <= BASE_MAX_AGE:
                    baseline = {"timestamp_utc": t.isoformat(),
                        "price_usd": numeric(base[1]), "liquidity_usd": numeric(base[2]),
                        "volume_m5":numeric(base[3]),
                        "buy_share":buy_share(base[4],base[5])}
            record = {"key": key, "mint": mint, "pair":pair,
                "strategies": item.get("strategies",[]),
                "signal_utc":signal.isoformat(),"baseline":baseline,
                "entry_quote":quote_info(item,0), "horizons":{}}
            for seconds in HORIZONS:
                target = signal+timedelta(seconds=seconds)
                horizon = {"target_utc":target.isoformat(),"market_status":"NOT_DUE"
                    if target>now else "MISSING_OR_LATE",
                    "observed_utc":None,"lag_seconds":None,
                    "price_change_pct":None,"liquidity_change_pct":None,
                    "volume_change_pct":None,"buy_share_change_pp":None}
                if target <= now and baseline:
                    end = min(now,target+timedelta(seconds=MAX_LAG_SECONDS))
                    sample = observation(conn,mint,pair,target,end,False)
                    if sample:
                        when = utc(sample[0])
                        if when and 0 <= (when-target).total_seconds() <= MAX_LAG_SECONDS:
                            share = buy_share(sample[4],sample[5])
                            oldshare = baseline["buy_share"]
                            horizon.update({"market_status":"OBSERVED",
                                "observed_utc":when.isoformat(),
                                "lag_seconds":round((when-target).total_seconds(),2),
                                "price_change_pct":change_pct(baseline["price_usd"],sample[1]),
                                "liquidity_change_pct":change_pct(baseline["liquidity_usd"],sample[2]),
                                "volume_change_pct":change_pct(baseline["volume_m5"],sample[3]),
                                "buy_share_change_pp":round((share-oldshare)*100,4)
                                    if share is not None and oldshare is not None else None})
                record["horizons"][str(seconds)] = horizon
            # The quote cohort timestamps +60/+300 relative to FIRST QUOTE, NOT signal.
            # Do not improperly align quote results to signal-relative liquidity horizons.
            record["quote_followups_from_first_quote"] = {
                "60":quote_info(item,60), "300":quote_info(item,300),
                "900":quote_info(item,900)}
            output.append(record)
    stats=Counter(h["market_status"] for item in output
                  for h in item["horizons"].values())
    return {"mode":"A_FIRST_MINUTES_PROSPECTIVE_READ_ONLY",
        "checked_utc":now.isoformat(),"signals":len(output),
        "horizon_samples":dict(stats), "records":output,
        "source_db_writes":0,"source_cohort_writes":0,
        "paper_trades_executed":0,"live_trades_executed":0,
        "limitations":[
            "Market horizons are relative to signal_utc; quote horizons are relative to first_quote_utc",
            "Horizon sample is first observed snapshot at/after target within 60s; absent is missing",
            "No backdated quote or executed fills; quote-only outcome cannot prove profitability",
            "Do not infer prediction from winners-only or sparse follow-up samples",
            "Liquidity may mechanically co-move with price; avoid interpreting correlation as cause"]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--cohort",required=True)
    a=p.parse_args()
    print(json.dumps(evaluate(a.db,a.cohort),indent=2))


if __name__=="__main__":
    main()
