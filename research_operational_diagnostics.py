"""Read-only operational diagnosis from already collected GTA6 research data.

No API, original DB writes, orders, timers, wallet or parameter changes.
Distinguishes historical Jupiter HTTP errors from recent errors and measures
exact-pair/data coverage by selection cohort on a bounded time window.
"""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_jupiter_minute_quotes import dt

ROOT = Path("/home/ubuntu/gta6-v32-gate-pilot")
AB = ROOT / "selection_shadow_comparison.sqlite"
JUPITER = ROOT / "jupiter_minute_quotes_state.json"


def _percentage(n, d):
    return round(100 * n / d, 2) if d else None


def selection_diagnosis(path, cutoff):
    result = {}
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        for cohort in ("legacy", "proposed"):
            row = db.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN status='LIQUIDITY_UNKNOWN' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='MARKET_DATA_UNAVAILABLE' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='QUOTE_MARKET_OBSERVED' THEN 1 ELSE 0 END),
                       COUNT(DISTINCT mint)
                FROM selection_cohort_samples
                WHERE observed_at>=? AND cohort=?
            """, (cutoff, cohort)).fetchone()
            n, liq_unknown, unavailable, observed, mints = row
            missing_col = "legacy_missing" if cohort == "legacy" else "proposed_missing"
            missing, cycles = db.execute(
                "SELECT COALESCE(SUM(" + missing_col + "),0),COUNT(*) "
                "FROM comparison_cycles WHERE observed_at>=? AND error IS NULL",
                (cutoff,)).fetchone()
            result[cohort] = {
                "observations": n,
                "unique_mints": mints,
                "known_market_data": observed or 0,
                "liquidity_unknown": liq_unknown or 0,
                "market_data_unavailable": unavailable or 0,
                "liquidity_unknown_pct": _percentage(liq_unknown or 0, n),
                "market_data_unavailable_pct": _percentage(unavailable or 0, n),
                "missing_exact_pair_cycle_count": missing,
                "successful_cycles": cycles,
                "missing_exact_pair_pct_of_selected": _percentage(missing, n),
            }
    return result


def jupiter_diagnosis(state, now, hours):
    recent_cutoff = now - timedelta(hours=hours)
    all_counts = Counter()
    recent_counts = Counter()
    http_statuses = Counter()
    recent_http_statuses = Counter()
    latest_error = None
    undated = 0
    for p in (state.get("positions") or {}).values():
        for q in (p.get("exit_quotes") or []):
            if not isinstance(q, dict):
                continue
            status = q.get("status", "UNKNOWN")
            stamp = dt(q.get("response_received_utc")) or dt(q.get("quoted_utc"))
            all_counts[status] += 1
            is_recent = stamp is not None and recent_cutoff <= stamp <= now
            if is_recent:
                recent_counts[status] += 1
            if stamp is None:
                undated += 1
            if status in ("QUOTE_HTTP_ERROR", "QUOTE_REQUEST_ERROR"):
                code = str(q.get("http_status") if q.get("http_status") is not None else "UNKNOWN")
                http_statuses[code] += 1
                if is_recent:
                    recent_http_statuses[code] += 1
                if stamp and (latest_error is None or stamp > latest_error):
                    latest_error = stamp
    failures = ("QUOTE_HTTP_ERROR", "QUOTE_REQUEST_ERROR")
    total = sum(all_counts.values())
    recent_total = sum(recent_counts.values())
    return {
        "all_exit_quotes": total,
        "historical_statuses": dict(all_counts),
        "historical_http_error_codes": dict(http_statuses),
        "last_hours": hours,
        "recent_exit_quotes": recent_total,
        "recent_statuses": dict(recent_counts),
        "recent_http_error_codes": dict(recent_http_statuses),
        "recent_failure_count": sum(recent_counts[s] for s in failures),
        "recent_failure_pct": _percentage(sum(recent_counts[s] for s in failures), recent_total),
        "latest_http_or_request_error_utc": latest_error.isoformat() if latest_error else None,
        "undated_exit_quotes": undated,
    }


def report(ab=AB, jupiter=JUPITER, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    selection = selection_diagnosis(ab, cutoff)
    state = json.loads(Path(jupiter).read_text())
    quotes = jupiter_diagnosis(state, now, hours)
    findings = []
    old = selection["legacy"]
    new = selection["proposed"]
    if new["missing_exact_pair_cycle_count"] > old["missing_exact_pair_cycle_count"]:
        findings.append("PROPOSED_HAS_MORE_MISSING_EXACT_PAIRS")
    if new["market_data_unavailable"] > old["market_data_unavailable"]:
        findings.append("PROPOSED_HAS_MORE_UNAVAILABLE_MARKET_ROWS")
    if quotes["recent_failure_count"]:
        findings.append("RECENT_JUPITER_QUOTE_ERRORS")
    return {
        "mode": "READ_ONLY_OPERATIONAL_DIAGNOSIS",
        "checked_utc": now.isoformat(), "hours": hours,
        "selection": selection, "jupiter": quotes, "findings": findings,
        "limitations": [
            "Exact-pair missing and market-data-unavailable overlap; do not add them together.",
            "The stored A/B snapshots cannot establish why a DexScreener pair is missing.",
            "Quotes can be older than the window and recent counts can be zero without new activity.",
            "The two selections overlap; observation totals are not independent token opportunities.",
            "Quote responses are not executable fills, stop-loss guarantees or realized returns.",
            "Read-only; no API requests or changes to running services."
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ab", default=str(AB))
    parser.add_argument("--jupiter", default=str(JUPITER))
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if args.hours < 1 or args.hours > 168:
        parser.error("--hours must be between 1 and 168")
    print(json.dumps(report(args.ab, args.jupiter, args.hours), indent=2))


if __name__ == "__main__":
    main()
