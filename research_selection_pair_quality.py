"""Read-only pair-quality attribution for legacy vs proposed selection.

Distinguishes shared selection from proposed-only and legacy-only observations.
Uses already recorded exact-pair snapshots; no new API calls or collector changes.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_b3_minute_safety_shadow import assess

DEFAULT = "/home/ubuntu/gta6-v32-gate-pilot/selection_shadow_comparison.sqlite"
STATUSES = ("QUOTE_MARKET_OBSERVED", "LIQUIDITY_UNKNOWN", "MARKET_DATA_UNAVAILABLE")


def _pct(value, total):
    return round(100 * value / total, 2) if total else None


def analyze(rows):
    cycles = defaultdict(lambda: {"legacy": {}, "proposed": {}})
    for observed, cohort, mint, pair, status, price, liq, volume, buys, sells, age in rows:
        if cohort not in ("legacy", "proposed"):
            continue
        cycles[observed][cohort][(mint, pair)] = (status, price, liq, volume, buys, sells, age)

    categories = {label: Counter() for label in ("shared", "legacy_only", "proposed_only")}
    unique = {label: set() for label in categories}
    consecutive_unavailable = defaultdict(int)
    longest_unavailable = defaultdict(int)
    sample_coverage = Counter()
    for stamp in sorted(cycles):
        old = cycles[stamp]["legacy"]
        new = cycles[stamp]["proposed"]
        shared = old.keys() & new.keys()
        groups = {
            "shared": {key: new[key] for key in shared},
            "legacy_only": {key: old[key] for key in old.keys() - shared},
            "proposed_only": {key: new[key] for key in new.keys() - shared},
        }
        sample_coverage["cycles"] += 1
        sample_coverage["shared_pairs"] += len(shared)
        # A shared pair must have the same market observation in both cohorts;
        # flag divergence rather than silently assuming exact simultaneity.
        sample_coverage["shared_status_mismatches"] += sum(
            old[key] != new[key] for key in shared
        )
        for label, items in groups.items():
            group = categories[label]
            for key, (status, price, liq, volume, buys, sells, age) in items.items():
                unique[label].add(key)
                group["observations"] += 1
                group[status] += 1
                if price is None or price <= 0:
                    group["price_missing_or_invalid"] += 1
                if age is None:
                    group["age_unknown"] += 1
                # Exact same age-inclusive B3 base filter; this is still not
                # a momentum match or an approved trade.
                base = assess((stamp, key[0], key[1], stamp, status,
                               price, liq, volume, buys, sells, age))["decision"]
                if base == "BASE_FILTER_PASS_MOMENTUM_UNVERIFIED":
                    group["base_pass_with_age"] += 1
                if label == "proposed_only":
                    if status == "MARKET_DATA_UNAVAILABLE":
                        consecutive_unavailable[key] += 1
                        longest_unavailable[key] = max(
                            longest_unavailable[key], consecutive_unavailable[key]
                        )
                    else:
                        consecutive_unavailable[key] = 0

    result = {}
    for label, group in categories.items():
        n = group["observations"]
        result[label] = {
            "observations": n,
            "unique_pairs": len(unique[label]),
            "market_observed": group["QUOTE_MARKET_OBSERVED"],
            "liquidity_unknown": group["LIQUIDITY_UNKNOWN"],
            "market_data_unavailable": group["MARKET_DATA_UNAVAILABLE"],
            "market_data_unavailable_pct": _pct(group["MARKET_DATA_UNAVAILABLE"], n),
            "liquidity_unknown_pct": _pct(group["LIQUIDITY_UNKNOWN"], n),
            "price_missing_or_invalid": group["price_missing_or_invalid"],
            "age_unknown": group["age_unknown"],
            "base_pass_with_age": group["base_pass_with_age"],
        }
    persistent = sorted(
        ((key, count) for key, count in longest_unavailable.items() if count >= 3),
        key=lambda x: (-x[1], x[0])
    )
    return {
        "groups": result,
        "coverage": dict(sample_coverage),
        "proposed_only_repeatedly_unavailable": {
            "pairs_with_3_plus_consecutive_samples": len(persistent),
            "longest_streak": persistent[0][1] if persistent else 0,
            "top_pairs": [
                {"mint": mint, "pair": pair, "max_consecutive_unavailable": count}
                for (mint, pair), count in persistent[:5]
            ],
        },
        "verdict": "PAIR_QUALITY_RESEARCH_ONLY_NOT_TRADING_PERFORMANCE",
        "limitations": [
            "Shared pairs are counted once, not twice.",
            "Market-data-unavailable does not distinguish missing exact pair from missing price.",
            "Consecutive means consecutive observations of a proposed-only pair, not uninterrupted minutes.",
            "No future information used in B3 base filtering; signals still require causal momentum.",
            "No price quotes, API calls, transactions, original database writes or selection switching.",
        ],
    }


def report(path=DEFAULT, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("""
            SELECT observed_at, cohort, mint, pair, status, price_usd,
                   liquidity_usd, volume_m5, buys_m5, sells_m5, pair_age_hours
            FROM selection_cohort_samples
            WHERE observed_at>=? ORDER BY observed_at, cohort, mint, pair
        """, (cutoff,)).fetchall()
    output = analyze(rows)
    output["window_hours"] = hours
    output["first_observation_utc"] = rows[0][0] if rows else None
    output["last_observation_utc"] = rows[-1][0] if rows else None
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=DEFAULT)
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        parser.error("--hours must be between 1 and 168")
    print(json.dumps(report(args.path, args.hours), indent=2))


if __name__ == "__main__":
    main()
