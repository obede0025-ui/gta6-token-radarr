"""Read-only audit of observed zero liquidity and post-transition market activity.

Never infer a real rug pull from DexScreener zero liquidity or rolling m5 volume.
"""
import argparse
import json
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def parse_time(stamp):
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def inspect(rows):
    """Rows: (mint,pair,observed_at,status,liquidity_usd,price_usd,volume_m5,buys_m5,sells_m5)."""
    groups = defaultdict(list)
    for mint, pair, stamp, status, lp, price, vol, buys, sells in rows:
        t = parse_time(stamp)
        if mint and pair and t:
            groups[mint, pair].append((t, status, lp, price, vol, buys, sells))

    summary = Counter()
    transition_gaps = []
    examples = []

    for (mint, pair), seq in groups.items():
        seq.sort(key=lambda item: item[0])
        market = [i for i, row in enumerate(seq) if row[1] == "QUOTE_MARKET_OBSERVED"]
        prior_market_idx = None
        seen_transition = False
        for i in market:
            row = seq[i]
            if prior_market_idx is None:
                prior_market_idx = i
                continue
            previous = seq[prior_market_idx]
            prior_market_idx = i
            if not (previous[2] is not None and previous[2] > 0 and row[2] == 0):
                continue
            summary["positive_to_zero_transitions"] += 1
            if not seen_transition:
                summary["pairs_with_positive_to_zero"] += 1
                seen_transition = True
            transition_gaps.append((row[0] - previous[0]).total_seconds())
            summary["at_zero_price_positive"] += int(row[3] is not None and row[3] > 0)
            summary["at_zero_m5_volume_positive"] += int(row[4] is not None and row[4] > 0)
            summary["at_zero_m5_tx_positive"] += int(
                (row[5] or 0) + (row[6] or 0) > 0
            )

            # Zero persistence requires an *actual* observed quote at or after +5m,
            # no more than 90 seconds late. UNKNOWN / UNAVAILABLE is not zero.
            target = row[0].timestamp() + 300
            valid = [
                later for later in seq[i + 1:]
                if later[1] == "QUOTE_MARKET_OBSERVED"
                and target <= later[0].timestamp() <= target + 90
            ]
            if valid:
                summary["five_min_observable"] += 1
                if valid[0][2] == 0:
                    summary["zero_at_five_min"] += 1
                elif valid[0][2] is not None and valid[0][2] > 0:
                    summary["positive_at_five_min"] += 1
                else:
                    summary["invalid_liquidity_at_five_min"] += 1
            else:
                summary["five_min_unobserved"] += 1

            # Look for activity in a later non-overlapping 5-min window, at >= +5m.
            # These m5 numbers do not prove post-zero trades on the same pair.
            later_m5 = next((later for later in seq[i + 1:]
                if later[1] == "QUOTE_MARKET_OBSERVED"
                and 300 <= (later[0] - row[0]).total_seconds() <= 390
            ), None)
            if later_m5:
                summary["later_m5_measured"] += 1
                summary["later_m5_volume_positive"] += int(
                    later_m5[4] is not None and later_m5[4] > 0
                )
                summary["later_m5_tx_positive"] += int(
                    (later_m5[5] or 0) + (later_m5[6] or 0) > 0
                )
            if len(examples) < 5:
                examples.append({
                    "mint": mint, "pair": pair,
                    "zero_observed_utc": row[0].isoformat(),
                    "five_min_outcome": ("zero" if valid and valid[0][2] == 0 else
                                         "positive" if valid and valid[0][2] is not None and valid[0][2] > 0
                                         else "unobserved"),
                })

    return {
        "mode": "READ_ONLY_ZERO_LIQUIDITY_REVIEW",
        **{k: summary[k] for k in (
            "positive_to_zero_transitions", "pairs_with_positive_to_zero",
            "at_zero_price_positive", "at_zero_m5_volume_positive",
            "at_zero_m5_tx_positive", "five_min_observable",
            "zero_at_five_min", "positive_at_five_min",
            "invalid_liquidity_at_five_min", "five_min_unobserved",
            "later_m5_measured", "later_m5_volume_positive",
            "later_m5_tx_positive"
        )},
        "median_transition_gap_seconds": round(median(transition_gaps), 1)
        if transition_gaps else None,
        "first_examples": examples,
        "limitations": [
            "Zero means API-reported zero, not independently confirmed empty pool.",
            "Rolling m5 volume and transactions do not prove execution after zero.",
            "An unknown/unavailable observation must never be treated as zero.",
            "Presence at +5 minutes does not prove every intermediate minute had zero liquidity.",
            "Analysis includes repeated transitions per pair; rows are not independent."
        ],
        "database_writes": 0,
        "trades_executed": 0
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minute", default="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite")
    args = ap.parse_args()
    p = Path(args.minute).expanduser().resolve(strict=True)
    with sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("""
            SELECT mint, pair, observed_at, status, liquidity_usd, price_usd,
                   volume_m5, buys_m5, sells_m5
            FROM minute_observations
            ORDER BY mint, pair, julianday(observed_at), observed_at, rowid
        """).fetchall()
    print(json.dumps(inspect(rows), indent=2))


if __name__ == "__main__":
    main()
