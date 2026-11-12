"""Forward-only observational volume threshold shadow (NO trades, no API calls).

Reads existing M1 V3, existing V3.2, and SQLite snapshots read-only.
Keeps ALL newly registered M1 tokens, including below-threshold and missing
feature cases. Outcome comes from ORIGINAL existing V3.2 quote history only;
unquoted candidates remain unknown, not counterfactually filled.

On first run bootstraps with no historical candidates. State is separate.
"""
import argparse
import fcntl
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from v32_quote_performance import evaluate

THRESHOLD = 3000.0


def parse(s):
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, TypeError):
        return None


def now_utc():
    return datetime.now(timezone.utc)


def checkpoint(path, data):
    target = Path(path)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temp.replace(target)


def snapshot(conn, mint, signal):
    # Use exactly what was observable at/before original signal timestamp.
    return conn.execute("""
        SELECT scanned_at, pair_address, volume_m5, liquidity_usd
        FROM snapshots WHERE token_address=?
          AND datetime(scanned_at)<=datetime(?)
        ORDER BY datetime(scanned_at) DESC LIMIT 1
    """, (mint, signal)).fetchone()


def decision(row):
    if row is None:
        return "UNKNOWN_NO_PRE_SIGNAL_SNAPSHOT", None
    try:
        volume = float(row[2])
        if volume < 0 or volume != volume:
            return "UNKNOWN_BAD_VOLUME", None
    except (ValueError, TypeError, OverflowError):
        return "UNKNOWN_BAD_VOLUME", None
    return ("VOLUME_GE_3000" if volume >= THRESHOLD else "VOLUME_LT_3000"), volume


def outcome(position):
    if not position:
        return {"outcome": "NO_V32_POSITION", "return_pct": None}
    result = evaluate(position, tp=10, sl=-10, horizon=900, cost_pp=2)
    return {
        "outcome": result.get("outcome"),
        "return_pct": (max(-100.0, float(result["net_quote_return_pct"]))
                       if result.get("net_quote_return_pct") is not None else None),
        "quote_utc": position.get("entry_quote_utc"),
    }


def step(state, m1, v32, conn):
    entries = m1.get("tracked")
    positions = v32.get("positions")
    if not isinstance(entries, dict) or not isinstance(positions, dict):
        raise ValueError("Invalid source state")
    if state.get("version") != 1 or "started_utc" not in state:
        raise ValueError("Invalid pilot state")
    start = parse(state["started_utc"])
    if not start:
        raise ValueError("Invalid pilot start timestamp")
    additions = 0
    for mint, tracked in sorted(entries.items()):
        if mint in state["candidates"] or not isinstance(tracked, dict):
            continue
        registered = parse(tracked.get("registered_utc"))
        if registered is None or registered <= start:
            # Strict forward-only. Missing registration time is not reclassified
            # using later state, preventing retrospective contamination.
            continue
        signal = parse(tracked.get("signal"))
        if not signal:
            continue
        row = snapshot(conn, mint, signal.isoformat())
        label, volume = decision(row)
        state["candidates"][mint] = {
            "mint": mint, "registered_utc": registered.isoformat(),
            "signal_utc": signal.isoformat(), "volume_group": label,
            "volume_m5_at_signal": volume,
            "snapshot_utc": row[0] if row else None,
            "liquidity_usd_at_signal": row[3] if row else None,
            "m1_status_at_registration_observation": tracked.get("status"),
            "quote": outcome(positions.get(mint)),
        }
        additions += 1
    for mint, item in state["candidates"].items():
        item["quote"] = outcome(positions.get(mint))
        item["last_observed_m1_status"] = (
            entries.get(mint, {}).get("status") if isinstance(entries.get(mint), dict)
            else "MISSING_FROM_ROLLING_STATE"
        )
    state["last_checked_utc"] = now_utc().isoformat()
    return additions


def summary(state):
    groups = {}
    for name in ("VOLUME_GE_3000", "VOLUME_LT_3000", "UNKNOWN_NO_PRE_SIGNAL_SNAPSHOT",
                 "UNKNOWN_BAD_VOLUME"):
        items = [x for x in state["candidates"].values() if x["volume_group"] == name]
        measured = [x["quote"]["return_pct"] for x in items
                    if x["quote"].get("return_pct") is not None]
        groups[name] = {
            "candidates": len(items),
            "with_quote_exit": len(measured),
            "unmeasured": len(items) - len(measured),
            "mean_observed_quote_return_pct": (
                round(statistics.mean(measured), 4) if measured else None),
            "worst_observed_quote_return_pct": (
                round(min(measured), 4) if measured else None),
            "quote_statuses": dict(Counter(x["quote"].get("outcome") for x in items)),
        }
    return groups


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--m1", default="/home/ubuntu/gta6-v63-test/m1_v3_state.json")
    p.add_argument("--v32", default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    p.add_argument("--db", default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    p.add_argument("--state", default="/home/ubuntu/gta6-v32-gate-pilot/volume_3000_shadow_state.json")
    args = p.parse_args()
    target = Path(args.state)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_name(target.name + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        m1 = json.loads(Path(args.m1).read_text())
        v32 = json.loads(Path(args.v32).read_text())
        if not target.exists():
            data = {
                "version": 1, "mode": "READ_ONLY_FORWARD_VOLUME_3000_SHADOW",
                "started_utc": now_utc().isoformat(), "candidates": {},
                "limitations": [
                    "Forward only, no counterfactual quotes for rejected candidates.",
                    "Missing V3.2 quotes are missing outcomes, never counted as zero returns.",
                    "Historical first-observed TP/SL quotes do not represent fills.",
                    "The candidate cohort is limited to the existing M1 selection.",
                    "The comparison is observational and may have strong selection bias.",
                    "No wallet, orders, Jupiter requests or existing state changes.",
                ],
            }
            checkpoint(target, data)
            print(json.dumps({"bootstrapped": True, "candidates": 0,
                              "note": "Earlier M1 registrations excluded"}))
            return
        data = json.loads(target.read_text())
        with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro",
                             uri=True) as conn:
            added = step(data, m1, v32, conn)
        checkpoint(target, data)
        print(json.dumps({
            "mode": data["mode"], "added": added,
            "tracked": len(data["candidates"]),
            "groups": summary(data),
            "last_checked_utc": data["last_checked_utc"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
