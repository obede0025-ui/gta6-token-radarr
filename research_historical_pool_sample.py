"""Deterministic historical zero-liquidity verification cohort (read-only).

Purpose: select cases for independent on-chain verification, not certify rug pulls.
No HTTP requests, writes, orders, market quotes, or services modified.
"""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def select_cases(rows, per_group=5):
    """Rows: mint, pair, timestamp, status, liquidity, price."""
    by_pair = defaultdict(list)
    for mint, pair, stamp, status, lp, price in rows:
        t = parse_time(stamp)
        if mint and pair and t:
            by_pair[mint, pair].append((t, status, lp, price))
    groups = defaultdict(list)
    counts = Counter()
    for (mint, pair), seq in by_pair.items():
        seq.sort(key=lambda r: r[0])
        last_quote = None
        for i, row in enumerate(seq):
            if row[1] != "QUOTE_MARKET_OBSERVED":
                continue
            if last_quote is not None:
                prev = seq[last_quote]
                if prev[2] is not None and prev[2] > 0 and row[2] == 0:
                    target = row[0].timestamp() + 300
                    future = next((r for r in seq[i + 1:]
                        if r[1] == "QUOTE_MARKET_OBSERVED"
                        and target <= r[0].timestamp() <= target + 90
                    ), None)
                    if future is None:
                        label = "UNOBSERVED_AT_5M"
                    elif future[2] == 0:
                        label = "STILL_ZERO_AT_5M"
                    elif future[2] is not None and future[2] > 0:
                        label = "RECOVERED_AT_5M"
                    else:
                        label = "INVALID_AT_5M"
                    counts[label] += 1
                    key = f"{mint}:{pair}:{row[0].isoformat()}"
                    groups[label].append({
                        "mint": mint, "pair": pair,
                        "previous_positive_utc": prev[0].isoformat(),
                        "first_zero_utc": row[0].isoformat(),
                        "previous_liquidity_usd": prev[2],
                        "zero_observed_price_usd": row[3],
                        "gap_seconds": (row[0] - prev[0]).total_seconds(),
                        "five_min_status": label,
                        "five_min_observed_utc": future[0].isoformat() if future else None,
                        "five_min_liquidity_usd": future[2] if future else None,
                        "_sort": hashlib.sha256(key.encode()).hexdigest()
                    })
            last_quote = i
    sample = []
    for label in ("STILL_ZERO_AT_5M", "RECOVERED_AT_5M",
                  "UNOBSERVED_AT_5M", "INVALID_AT_5M"):
        for case in sorted(groups[label], key=lambda item: item["_sort"])[:per_group]:
            sample.append({k: v for k, v in case.items() if k != "_sort"})
    return {
        "mode": "READ_ONLY_HISTORICAL_POOL_VERIFICATION_SAMPLE",
        "population_by_outcome": dict(counts),
        "sampling": "deterministic hash-stratified; not random proof of representativeness",
        "sample": sample,
        "verification_checklist": [
            "Identify actual on-chain pool program and reserve accounts for the exact pair.",
            "Map recorded UTC event time to historical Solana slot(s).",
            "Retrieve historical reserves at before, at, and after zero using an archival provider or replay.",
            "Check migration and alternate pools independently; observed pair list may be incomplete.",
            "Keep UNVERIFIED label unless pool reserves and transaction evidence support another finding."
        ],
        "limitations": [
            "Current DexScreener or current getAccountInfo do NOT establish historical reserves.",
            "No trade was executed or simulated.",
            "Strata are selected after a 5-minute outcome and are for diagnosis, never predictive training."
        ],
        "database_writes": 0,
        "trades_executed": 0
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute", default="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite")
    ap.add_argument("--per-group", type=int, default=5)
    args = ap.parse_args()
    if not 1 <= args.per_group <= 20:
        ap.error("--per-group must be between 1 and 20")
    path = Path(args.minute).expanduser().resolve(strict=True)
    with sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("""
            SELECT mint, pair, observed_at, status, liquidity_usd, price_usd
            FROM minute_observations
            ORDER BY mint, pair, julianday(observed_at), observed_at, rowid
        """).fetchall()
    print(json.dumps(select_cases(rows, args.per_group), indent=2))


if __name__ == "__main__":
    main()
