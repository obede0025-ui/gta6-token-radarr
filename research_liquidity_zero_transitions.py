"""Read-only report: transitions from positive to zero liquidity, possible pair changes.

All windows are observation-based, not proof of on-chain rug pulls/migrations.
Never changes databases or trading services.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def read_db(path):
    p = Path(path).expanduser().resolve(strict=True)
    con = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def pct_change(start, end):
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1) * 100


def analyze_rows(market_rows, learning_pairs=()):
    """Rows: (mint, pair, timestamp, liquidity, price, m5_volume, buys, sells)."""
    by_pair = defaultdict(list)
    learning = defaultdict(set)
    for mint, pair in learning_pairs:
        if mint and pair:
            learning[mint].add(pair)

    for mint, pair, stamp, lp, price, volume, buys, sells in market_rows:
        t = parse_time(stamp)
        if mint and pair and t is not None:
            by_pair[(mint, pair)].append((t, lp, price, volume, buys, sells))

    minute_pairs = defaultdict(set)
    for mint, pair in by_pair:
        minute_pairs[mint].add(pair)

    counts = Counter()
    gaps = []
    before_price_changes = []
    before_volume_changes = []
    transitions = []
    sample = []

    for (mint, pair), sequence in by_pair.items():
        sequence.sort(key=lambda r: r[0])
        zero = any(row[1] == 0 for row in sequence)
        if not zero:
            continue

        counts["pairs_with_zero"] += 1
        counts["always_zero"] += int(all(row[1] == 0 for row in sequence))
        counts["starts_zero"] += int(sequence[0][1] == 0)
        counts["starts_positive"] += int(sequence[0][1] is not None and sequence[0][1] > 0)
        has_recovery = any(
            a[1] == 0 and b[1] is not None and b[1] > 0
            for a, b in zip(sequence, sequence[1:])
        )
        counts["pairs_with_zero_to_positive"] += int(has_recovery)

        # Only first observed positive -> 0 transition per pair to avoid repeated counting.
        transition_index = next((
            i for i in range(1, len(sequence))
            if sequence[i - 1][1] is not None and sequence[i - 1][1] > 0
            and sequence[i][1] == 0
        ), None)
        if transition_index is None:
            continue

        counts["pairs_positive_to_zero"] += 1
        prev, current = sequence[transition_index - 1:transition_index + 1]
        gap = (current[0] - prev[0]).total_seconds()
        if gap >= 0:
            gaps.append(gap)
        counts["transition_gap_over_120_seconds"] += int(gap > 120)
        counts["transition_gap_over_600_seconds"] += int(gap > 600)

        # Strictly pre-transition trend from two positive observations; no future leakage.
        previous_positive = sequence[transition_index - 2] if transition_index >= 2 else None
        price_change = pct_change(previous_positive[2], prev[2]) if previous_positive else None
        volume_change = pct_change(previous_positive[3], prev[3]) if previous_positive else None
        if previous_positive is not None and previous_positive[1] is not None and previous_positive[1] > 0:
            if price_change is not None:
                before_price_changes.append(price_change)
            if volume_change is not None:
                before_volume_changes.append(volume_change)

        # Alternative pair must have a positive observation within ±15 min of transition.
        other = [
            (p, row) for (m, p), seq in by_pair.items() if m == mint and p != pair
            for row in seq if row[1] is not None and row[1] > 0
            and abs((row[0] - current[0]).total_seconds()) <= 900
        ]
        counts["alternate_pair_in_minute_db_near_transition"] += int(bool(other))
        counts["alternate_pair_known_in_learning_db"] += int(
            any(p != pair for p in learning[mint])
        )
        transitions.append({
            "mint": mint, "pair": pair, "timestamp_utc": current[0].isoformat(),
            "gap_seconds": gap, "price_change_pre_transition_pct": price_change,
            "volume_change_pre_transition_pct": volume_change,
            "alternative_pair_with_positive_liquidity_near_transition": bool(other),
            "alternative_pair_known_in_learning": any(p != pair for p in learning[mint]),
            "recovered_positive_liquidity_on_same_pair": has_recovery,
        })

    return {
        "pairs_with_zero": counts["pairs_with_zero"],
        "always_zero": counts["always_zero"],
        "starts_zero": counts["starts_zero"],
        "starts_positive": counts["starts_positive"],
        "pairs_positive_to_zero": counts["pairs_positive_to_zero"],
        "pairs_with_zero_to_positive": counts["pairs_with_zero_to_positive"],
        "median_gap_before_first_zero_seconds": round(median(gaps), 1) if gaps else None,
        "transition_gap_over_120_seconds": counts["transition_gap_over_120_seconds"],
        "transition_gap_over_600_seconds": counts["transition_gap_over_600_seconds"],
        "median_price_change_before_transition_pct": round(median(before_price_changes), 2) if before_price_changes else None,
        "pre_transition_price_samples": len(before_price_changes),
        "median_m5_volume_change_before_transition_pct": round(median(before_volume_changes), 2) if before_volume_changes else None,
        "pre_transition_volume_samples": len(before_volume_changes),
        "alternate_pair_with_positive_liquidity_near_transition": counts["alternate_pair_in_minute_db_near_transition"],
        "alternate_pair_known_in_learning_db": counts["alternate_pair_known_in_learning_db"],
        "first_examples": transitions[:5],
        "limitations": [
            "Pairs may migrate to platforms not captured by either database.",
            "A zero liquidity API value is not proof of a rug pull.",
            "Earlier observations can be minutes apart; first observed zero is not an exact event time.",
            "A known alternate pair is not proof of migration.",
        ],
        "database_writes": 0,
        "trades_executed": 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute", default="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite")
    ap.add_argument("--learning", default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    args = ap.parse_args()

    with read_db(args.minute) as db:
        market_rows = db.execute("""
            SELECT mint, pair, observed_at, liquidity_usd, price_usd,
                   volume_m5, buys_m5, sells_m5
            FROM minute_observations
            WHERE status='QUOTE_MARKET_OBSERVED'
            ORDER BY julianday(observed_at), observed_at
        """).fetchall()

    with read_db(args.learning) as db:
        learning_pairs = db.execute("""
            SELECT DISTINCT token_address, pair_address FROM snapshots
            WHERE token_address IS NOT NULL AND pair_address IS NOT NULL
        """).fetchall()

    print(json.dumps(analyze_rows(market_rows, learning_pairs), indent=2))


if __name__ == "__main__":
    main()
