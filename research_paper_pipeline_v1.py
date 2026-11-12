"""Prospective paper-only decision ledger from existing M1 and V3.2 states.

No API requests, no trades, no writes to source states or learning.db.
Decisions freeze signal-time features and never infer fills from a quote.
"""
import argparse
import fcntl
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from v32_quote_performance import evaluate

M1 = "/home/ubuntu/gta6-v63-test/m1_v3_state.json"
V32 = "/home/ubuntu/gta6-v63-test/m1_v3_2_state.json"
DB = "/home/ubuntu/gta6-paper-vps/data/learning.db"
STATE = "/home/ubuntu/gta6-v32-gate-pilot/paper_pipeline_v1_state.json"

RULES = {"min_volume_m5_usd": 3000, "min_liquidity_usd": 20000,
         "max_pair_age_hours": 24, "buy_sell_required": True,
         "tp_pct": 10, "sl_pct": -10, "horizon_seconds": 900,
         "assumed_extra_cost_pp": 2}


def parse(value):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def finite(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def snapshot(db, mint, pair, stamp):
    return db.execute("""
        SELECT scanned_at, pair_address, volume_m5, liquidity_usd,
               buys_m5, sells_m5, pair_age_hours, price_usd
        FROM snapshots
        WHERE token_address=? AND pair_address=?
          AND datetime(scanned_at)<=datetime(?)
        ORDER BY datetime(scanned_at) DESC LIMIT 1
    """, (mint, pair, stamp)).fetchone()


def gate(row):
    if row is None:
        return "UNKNOWN_NO_SIGNAL_SNAPSHOT", ["NO_SIGNAL_SNAPSHOT"]
    vol, liq, buys, sells, age, price = map(finite, row[2:8])
    if any(x is None for x in (vol, liq, buys, sells, age, price)):
        return "UNKNOWN_INVALID_FEATURES", ["INVALID_FEATURES"]
    failures = []
    if price <= 0: failures.append("INVALID_PRICE")
    if vol < RULES["min_volume_m5_usd"]: failures.append("VOLUME_BELOW_3000")
    if liq < RULES["min_liquidity_usd"]: failures.append("LIQUIDITY_BELOW_20000")
    if buys <= sells: failures.append("BUY_SELL_IMBALANCE")
    if not 0 <= age <= RULES["max_pair_age_hours"]: failures.append("PAIR_AGE_INVALID")
    return ("REJECT" if failures else "PASS"), failures


def observe(position):
    if not isinstance(position, dict):
        return {"quote_outcome": "NO_V32_POSITION", "net_quote_return_pct": None,
                "entry_quote_utc": None, "source_status": None}
    r = evaluate(position, tp=RULES["tp_pct"], sl=RULES["sl_pct"],
                 horizon=RULES["horizon_seconds"], cost_pp=RULES["assumed_extra_cost_pp"])
    ret = finite(r.get("net_quote_return_pct"))
    return {"quote_outcome": r["outcome"],
            "net_quote_return_pct": max(-100, ret) if ret is not None else None,
            "entry_quote_utc": position.get("entry_quote_utc"),
            "source_status": position.get("status"),
            "exit_quote_count": len(position.get("exit_quotes") or [])}


def advance(state, m1, v32, db):
    if state.get("version") != 1 or state.get("mode") != "PAPER_PIPELINE_V1_SHADOW":
        raise ValueError("Invalid pilot state")
    if state.get("rules") != RULES:
        raise ValueError("Rules changed; refusing mixed-policy cohort")
    entries, positions = m1.get("tracked"), v32.get("positions")
    if m1.get("version") != 1 or not isinstance(entries, dict) or not isinstance(positions, dict):
        raise ValueError("Invalid source states")
    cutoff = parse(state["started_utc"])
    if cutoff is None:
        raise ValueError("Invalid bootstrap timestamp")
    added = 0
    for mint, item in sorted(entries.items()):
        if mint in state["candidates"] or not isinstance(item, dict):
            continue
        registered = parse(item.get("registered_utc"))
        signal = parse(item.get("signal"))
        if registered is None or registered <= cutoff or signal is None:
            continue  # bootstrap barrier; no retrospective candidates
        if not isinstance(item.get("pair"), str):
            continue
        row = snapshot(db, mint, item["pair"], signal.isoformat())
        decision, reasons = gate(row)
        state["candidates"][mint] = {
            "mint": mint, "registered_utc": registered.isoformat(),
            "signal_utc": signal.isoformat(), "pair": item["pair"],
            "signal_snapshot_utc": row[0] if row else None,
            "signal_volume_m5_usd": finite(row[2]) if row else None,
            "signal_liquidity_usd": finite(row[3]) if row else None,
            "decision": decision, "reasons": reasons,
            "source_m1_status_last_seen": item.get("status"),
            "quote": observe(positions.get(mint)),
        }
        added += 1
    for mint, candidate in state["candidates"].items():
        # Never revise the frozen entry decision using later observations.
        candidate["quote"] = observe(positions.get(mint))
        if isinstance(entries.get(mint), dict):
            candidate["source_m1_status_last_seen"] = entries[mint].get("status")
    state["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
    return added


def summary(state):
    candidates = list(state["candidates"].values())
    counts = dict(Counter(c["decision"] for c in candidates))
    quoted = [c for c in candidates if c["decision"] == "PASS" and
              c["quote"]["net_quote_return_pct"] is not None]
    missing = [c for c in candidates if c["decision"] == "PASS" and
               c["quote"]["net_quote_return_pct"] is None]
    returns = [c["quote"]["net_quote_return_pct"] for c in quoted]
    rejected_with_quote = sum(
        c["decision"] == "REJECT" and c["quote"]["entry_quote_utc"] is not None
        for c in candidates
    )
    return {
        "mode": state["mode"], "started_utc": state["started_utc"],
        "last_checked_utc": state.get("last_checked_utc"),
        "tracked_new_candidates": len(candidates), "decisions": counts,
        "passed_with_observable_quote_exit": len(quoted),
        "passed_without_observable_exit": len(missing),
        "passed_quote_outcomes": dict(Counter(c["quote"]["quote_outcome"]
                                             for c in candidates if c["decision"] == "PASS")),
        "passed_mean_net_quote_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "passed_worst_net_quote_return_pct": min(returns) if returns else None,
        "passed_extreme_quote_losses_under_minus_40pct": sum(x < -40 for x in returns),
        "rejected_with_original_v32_entry_quote": rejected_with_quote,
        "limitations": [
            "Quotes are not executions or realized PnL.",
            "V3.2 quote sampling is selective; missing outcomes remain unknown.",
            "Rejected tokens may have quotes because original V3.2 uses different rules.",
            "M1 source has pre-filtered candidates; no inference about rejected B3 tokens.",
            "Results are prospective only after bootstrap; no backfilled old signals.",
            "Every decision uses one snapshot dated at or before the signal.",
            "Assumed 2 percentage points cost is a scenario, not measured execution fees.",
            "A TP/SL between quotes may be missed; delayed quotes are not fills."
        ],
    }


def atomic(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--m1", default=M1)
    p.add_argument("--v32", default=V32)
    p.add_argument("--db", default=DB)
    p.add_argument("--state", default=STATE)
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()
    target = Path(args.state)
    if args.report_only:
        print(json.dumps(summary(json.loads(target.read_text())), indent=2))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_name(target.name + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        m1 = json.loads(Path(args.m1).read_text())
        v32 = json.loads(Path(args.v32).read_text())
        if not target.exists():
            if m1.get("version") != 1 or not isinstance(m1.get("tracked"), dict):
                raise ValueError("Invalid M1 source")
            state = {"version": 1, "mode": "PAPER_PIPELINE_V1_SHADOW",
                     "started_utc": datetime.now(timezone.utc).isoformat(),
                     "rules": RULES, "candidates": {}}
            with target.open("x") as f:
                json.dump(state, f, indent=2)
            print(json.dumps({"bootstrapped": True, "new_candidates": 0,
                              "note": "Only future M1 registrations; old tokens excluded"}))
            return
        state = json.loads(target.read_text())
        with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as db:
            added = advance(state, m1, v32, db)
        atomic(target, state)
        print(json.dumps({"added": added, **summary(state)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
