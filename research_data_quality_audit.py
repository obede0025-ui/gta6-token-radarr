"""Read-only, repeatable quality report for existing GTA6 research databases.

Run on VPS:
python research_data_quality_audit.py
No schema changes, writes, signal changes, trades or service changes.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def parse_timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def connect_read_only(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Database path is not a file")
    db = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def require_columns(db, table, names):
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if table not in tables:
        raise ValueError("Missing table: " + table)
    columns = {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}
    if not set(names) <= columns:
        raise ValueError("Missing columns in " + table + ": " +
                         ", ".join(sorted(set(names) - columns)))


def learning_report(db):
    require_columns(db, "snapshots", [
        "token_address", "pair_address", "scanned_at", "price_usd",
        "liquidity_usd", "pair_age_hours", "source"])
    first = {}
    per_pair = defaultdict(list)
    malformed_rows = 0
    query = """SELECT token_address, pair_address, scanned_at, price_usd,
                      liquidity_usd, pair_age_hours, source
               FROM snapshots
               ORDER BY julianday(scanned_at), scanned_at, token_address"""
    for mint, pair, stamp, price, liq, age, source in db.execute(query):
        time = parse_timestamp(stamp)
        if time is None:
            malformed_rows += 1
        if not mint:
            continue
        first.setdefault(mint, (pair, time, price, liq, age, source))
        if pair and time is not None:
            per_pair[(mint, pair)].append(time)
    issues, sources = Counter(), Counter()
    ages = defaultdict(list)
    valid = 0
    for pair, time, price, liq, age, source in first.values():
        name = source or "UNKNOWN"
        sources[name] += 1
        if age is not None and age >= 0:
            ages[name].append(age * 60)
        bad_price = price is None or price <= 0
        bad_liq = liq is None or liq <= 0
        issues["invalid_initial_price"] += int(bad_price)
        issues["invalid_initial_liquidity"] += int(bad_liq)
        issues["invalid_price_or_liquidity"] += int(bad_price or bad_liq)
        issues["missing_initial_pair"] += int(not pair)
        issues["invalid_initial_timestamp"] += int(time is None)
        valid += int(not bad_price and not bad_liq)
    gaps = []
    for times in per_pair.values():
        times.sort()
        gaps.extend((b - a).total_seconds() for a, b in zip(times, times[1:])
                    if b > a)
    return {
        "unique_tokens": len(first),
        "valid_initial_market_data": valid,
        "issue_counts_overlapping": dict(issues),
        "invalid_timestamp_rows": malformed_rows,
        "median_pair_observation_gap_seconds": round(median(gaps), 2) if gaps else None,
        "first_sources": [
            {"source": name, "count": n,
             "median_pair_age_minutes": round(median(ages[name]), 2)
             if ages[name] else None}
            for name, n in sources.most_common()
        ]
    }, set(first)


def minute_report(db, learning_mints):
    require_columns(db, "minute_observations", [
        "observed_at", "mint", "pair", "status", "price_usd", "liquidity_usd"])
    by_pair, mints, pairs = {}, set(), set()
    count, invalid_times, invalid_price, invalid_liquidity = 0, 0, 0, 0
    gaps = []
    query = """SELECT observed_at, mint, pair, price_usd, liquidity_usd
               FROM minute_observations
               WHERE status='QUOTE_MARKET_OBSERVED'
               ORDER BY julianday(observed_at), observed_at"""
    for stamp, mint, pair, price, liq in db.execute(query):
        count += 1
        invalid_price += int(price is None or price <= 0)
        invalid_liquidity += int(liq is None or liq <= 0)
        time = parse_timestamp(stamp)
        if time is None:
            invalid_times += 1
        if not mint or not pair:
            continue
        mints.add(mint)
        pairs.add((mint, pair))
        if time is None:
            continue
        key = (mint, pair)
        if key in by_pair:
            seconds = (time - by_pair[key]).total_seconds()
            if seconds > 0:
                gaps.append(seconds)
        by_pair[key] = time
    status_rows = db.execute("""
        SELECT COALESCE(status, '(NULL)') AS label,
               COUNT(*),
               SUM(CASE WHEN liquidity_usd IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN liquidity_usd = 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN liquidity_usd < 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN liquidity_usd > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN price_usd IS NULL THEN 1 ELSE 0 END)
        FROM minute_observations
        GROUP BY COALESCE(status, '(NULL)')
        ORDER BY COUNT(*) DESC, label
    """).fetchall()
    status_breakdown = [
        {
            "status": label, "observations": total,
            "liquidity_null": null_count,
            "liquidity_zero": zero_count,
            "liquidity_negative": negative_count,
            "liquidity_positive": positive_count,
            "price_null": price_null,
        }
        for (label, total, null_count, zero_count,
             negative_count, positive_count, price_null) in status_rows
    ]
    return {
        "status_breakdown_all_observations": status_breakdown,
        "observations": count,
        "unique_tokens": len(mints),
        "unique_pairs": len(pairs),
        "tokens_in_both_databases": len(mints & learning_mints),
        "invalid_timestamp_rows": invalid_times,
        "invalid_price_rows": invalid_price,
        "invalid_liquidity_rows": invalid_liquidity,
        "median_pair_observation_gap_seconds": round(median(gaps), 2) if gaps else None
    }


def audit(learning, minute):
    with connect_read_only(learning) as db:
        learning_result, mints = learning_report(db)
    with connect_read_only(minute) as db:
        minute_result = minute_report(db, mints)
    return {
        "mode": "READ_ONLY_RESEARCH_DATA_QUALITY",
        "learning": learning_result,
        "minute_market": minute_result,
        "limitations": [
            "First stored snapshot is not necessarily launch time.",
            "Issue categories overlap; do not add them together.",
            "This is all available history, not a rolling 7-day cohort.",
            "Zero liquidity is not automatically proof of a real liquidity collapse.",
            "Market prices are not executed trade prices."
        ],
        "database_writes": 0,
        "trades_executed": 0
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learning",
                        default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    parser.add_argument("--minute",
                        default="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite")
    args = parser.parse_args()
    print(json.dumps(audit(args.learning, args.minute), indent=2))


if __name__ == "__main__":
    main()
