"""Focused GTA6 research: A vs B2, historical coverage and fresh shortlist.

Pure read-only SQLite and JSON report. No network, timers, trading or source
writes. Historic scanner data is diagnostic, NOT a backtested execution.
Only currently fresh signals can appear in the prospective shortlist.
"""
import argparse
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

MAX_FRESH_SEC = 120
MAX_SHORTLIST = 3


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, OverflowError):
        return None


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def portfolio_metrics(state, group, stake=25):
    closed = state.get("closed", [])
    open_ = state.get("open", {})
    if isinstance(closed, dict):
        closed = closed.get(group, [])
    if isinstance(open_, dict) and group in open_ and isinstance(open_[group], dict):
        open_ = open_[group]
    if not isinstance(closed, list) or not isinstance(open_, dict):
        raise ValueError("Unrecognized portfolio layout")
    vals = [number(r.get("pnl_usd")) for r in closed if isinstance(r, dict)]
    vals = [v for v in vals if v is not None]
    mints_closed = {r.get("token") for r in closed if isinstance(r, dict) and r.get("token")}
    return {
        "closed_count": len(closed), "valid_closed_pnl_count": len(vals),
        "closed_pnl_usd": round(sum(vals), 5),
        "mean_closed_pnl_usd": round(sum(vals)/len(vals), 5) if vals else None,
        "median_closed_pnl_usd": round(median(vals), 5) if vals else None,
        "closed_wins": sum(x > 0 for x in vals),
        "open_count": len(open_),
        "open_stake_at_risk_usd": len(open_) * stake,
        "zero_recovery_stress_pnl_usd": round(sum(vals) - len(open_) * stake, 5),
        "closed_mints": mints_closed,
        "open_mints": set(open_),
    }


def paired_comparison(abc, b2):
    a = portfolio_metrics(abc, "A")
    b = portfolio_metrics(b2, "B2")
    overlap = (a["closed_mints"] | a["open_mints"]) & (b["closed_mints"] | b["open_mints"])
    for row in (a, b):
        row.pop("closed_mints")
        row.pop("open_mints")
    # Not a matched performance experiment: only measures duplicated exposure.
    return {
        "strategy_a": a, "strategy_b2": b,
        "shared_exposed_mints": len(overlap),
        "comparison_status": "UNPAIRED_HISTORICAL_COHORTS",
        "winner_after_all_open_exits": "UNPROVEN",
        "notes": ["Closed-only P/L ignores open exposures; zero-recovery is stress, not current mark.",
                  "Overlapping mints and different candidate time windows prevent causal comparisons.",
                  "No actual fills, current mark-to-market, slippage verification or net realizable profit."]
    }


def signal_labels(history, price, vol):
    """Exact A/B three-snapshot momentum conditions (after spacing validation)."""
    if len(history) < 2 or price is None or vol is None:
        return []
    old, prev = history[-2:]
    p1, v1 = old[1], old[2]
    p2, v2 = prev[1], prev[2]
    if any(v is None for v in (p1, v1, p2, v2)) or p1 <= 0 or p2 <= 0:
        return []
    gain = price/p1 - 1
    result = []
    if p1 < p2 < price and v1 < v2 < vol and gain >= .02:
        result.append("A")
    if gain >= .05 and price > p2:
        result.append("B2_SIGNAL_PROXY")
    return result


def diagnose_and_shortlist(conn, now, hours=24, max_shortlist=3):
    if not 1 <= hours <= 168 or not 1 <= max_shortlist <= MAX_SHORTLIST:
        raise ValueError("hours 1..168, shortlist 1..3")
    cutoff = now - timedelta(hours=hours)
    # SQLite ISO 8601 timestamps are parsed with Python (not SQLite datetime lexicographical comparison).
    # Pull recent observations with a 1 day buffer for boundary history. Source is read-only.
    fetch_since = (cutoff - timedelta(hours=1)).isoformat()
    rows = conn.execute("""
        SELECT scanned_at,token_address,pair_address,price_usd,liquidity_usd,
               volume_m5,buys_m5,sells_m5,pair_age_hours
        FROM snapshots WHERE scanned_at >= ?
        ORDER BY scanned_at,token_address,pair_address
    """, (fetch_since,))
    histories = {}
    gap_samples = 0
    gaps_20m = 0
    counted = 0
    qualifying_market = 0
    blocked = Counter()
    candidates = {}
    signal_counts = Counter()
    max_observed_at = None
    for stamp, mint, pair, price, liq, vol, buys, sells, pair_age in rows:
        when = timestamp(stamp)
        if when is None or not isinstance(mint, str) or not isinstance(pair, str):
            continue
        key = (mint, pair)
        history = histories.setdefault(key, [])
        if history:
            gap = (when - history[-1][0]).total_seconds()
            if when >= cutoff:
                gap_samples += 1
                gaps_20m += gap > 1200
            # The original engine only permits sequential intervals 3.5..20m.
            if gap < 210 or gap > 1200:
                history.clear()
        price, liq, vol, buys, sells, pair_age = map(
            number, (price, liq, vol, buys, sells, pair_age))
        if when >= cutoff:
            counted += 1
            if max_observed_at is None or when > max_observed_at:
                max_observed_at = when
            failures = []
            if price is None or price <= 0:
                failures.append("INVALID_PRICE")
            if liq is None or liq < 20000:
                failures.append("LIQUIDITY_LT_20000_OR_UNKNOWN")
            if vol is None or vol < 250:
                failures.append("VOLUME_LT_250_OR_UNKNOWN")
            if buys is None or sells is None or buys <= sells:
                failures.append("NO_BUY_PRESSURE")
            if pair_age is None or not 0 <= pair_age <= 24:
                failures.append("PAIR_AGE_OUTSIDE_0_24H")
            blocked.update(failures)
            if not failures:
                qualifying_market += 1
                labels = signal_labels(history, price, vol)
                signal_counts.update(labels)
                age = (now - when).total_seconds()
                if labels and 0 <= age <= MAX_FRESH_SEC:
                    prev = candidates.get(mint)
                    row = {"mint": mint, "pair": pair,
                           "observed_utc": when.isoformat(timespec="seconds"),
                           "age_seconds": round(age, 2), "strategies": labels,
                           "liquidity_usd": liq, "volume_5m_usd": vol,
                           "buys_m5": buys, "sells_m5": sells,
                           "pair_age_hours": pair_age,
                           "quote_status": "NOT_REQUESTED",
                           "paper_trade_approval": "BLOCK",
                           "live_trade_approval": "BLOCK"}
                    if prev is None or stamp > prev["observed_utc"]:
                        candidates[mint] = row
        if price is not None and price > 0:
            history.append((when, price, vol))
            if len(history) > 2:
                history.pop(0)
        else:
            history.clear()
    shortlist = sorted(candidates.values(),
                       key=lambda r: (-r["liquidity_usd"], -r["volume_5m_usd"], r["mint"]))[:max_shortlist]
    return {
        "historical_coverage": {
            "window_hours": hours, "snapshot_rows": counted,
            "last_snapshot_utc": max_observed_at.isoformat() if max_observed_at else None,
            "gaps_with_predecessor": gap_samples, "gaps_over_20min": gaps_20m,
            "gap_rate_pct": round(100*gaps_20m/gap_samples, 2) if gap_samples else None,
            "market_quality_pass_observations": qualifying_market,
            "overlapping_market_rejection_counts": dict(blocked),
            "a_signal_proxy_observations": signal_counts["A"],
            "b2_signal_proxy_observations": signal_counts["B2_SIGNAL_PROXY"],
            "warning": "Repeated observations are not independent; signals are pre-capacity research proxies, not engine trades."
        },
        "fresh_shortlist": shortlist,
        "shortlist_status": "READY_FOR_QUOTE_ONLY_RESEARCH" if shortlist else "NO_FRESH_MATCHING_SIGNALS",
        "shortlist_policy": "Strict 120s freshness, >=$20k liquidity, >=$250 5m volume, buys>sells, 0..24h pair age",
        "quote_calls": 0,
        "trades_executed": 0,
    }


def analyse(db, abc, b2, now=None, hours=24):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    abc_state = json.loads(Path(abc).read_text())
    b2_state = json.loads(Path(b2).read_text())
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        scans = diagnose_and_shortlist(conn, now, hours)
    return {
        "mode": "FOCUS_A_B2_HISTORY_AND_FRESH_SHORTLIST_READ_ONLY",
        "checked_utc": now.isoformat(timespec="seconds"),
        "comparison": paired_comparison(abc_state, b2_state),
        **scans,
        "source_db_writes": 0, "portfolio_writes": 0,
        "live_orders_executed": 0,
        "next_step": "Run V3.5 quote-only evidence only for forward-observed candidates; no backdated quotes.",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--abc", required=True)
    p.add_argument("--b2", required=True)
    p.add_argument("--hours", type=int, default=24)
    a = p.parse_args()
    print(json.dumps(analyse(a.db, a.abc, a.b2, hours=a.hours), indent=2))


if __name__ == "__main__":
    main()
