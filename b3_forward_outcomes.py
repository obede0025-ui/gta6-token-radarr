"""Offline, forward-only B3 outcome audit. Not a PnL or fill simulator."""
import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from b3_candidate_audit import parsed

HORIZONS_MINUTES = (15, 60)
MAX_SAMPLE_LAG_MINUTES = 10
MAX_EXAMPLES = 10

def valid_positive(x):
    try:
        v = float(x)
        return v if math.isfinite(v) and v > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None

def audit(db_path, state_path, now=None):
    now = now or datetime.now(timezone.utc)
    db_path, state_path = Path(db_path), Path(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("version") != 1 or not isinstance(state.get("events"), list):
        raise ValueError("Invalid B3 shadow state")
    if not db_path.is_file():
        raise FileNotFoundError("Market database missing")
    records = []
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        for event in state["events"]:
            if not isinstance(event, dict):
                continue
            mint, pair = event.get("mint"), event.get("pair")
            if not isinstance(mint, str) or not isinstance(pair, str) or not mint or not pair:
                continue
            try:
                t0 = parsed(event["scanned_at"])
            except (ValueError, TypeError, KeyError):
                continue
            if t0 > now:
                continue
            entry = valid_positive(event.get("price_usd"))
            decision = event.get("decision", "UNKNOWN")
            sample = {"mint": mint, "pair": pair, "scanned_at": event["scanned_at"],
                      "decision": decision, "reasons": event.get("reasons", []),
                      "entry_price_usd": entry, "horizons": {}}
            for minutes in HORIZONS_MINUTES:
                target = t0 + timedelta(minutes=minutes)
                label = str(minutes) + "m"
                if target > now:
                    sample["horizons"][label] = {"status": "PENDING"}
                    continue
                # Samples cannot precede horizon target; same pool only, bounded 10m lag.
                row = conn.execute(
                    """SELECT scanned_at,price_usd FROM snapshots
                    WHERE token_address=? AND pair_address=?
                      AND scanned_at>=? AND scanned_at<=?
                    ORDER BY scanned_at ASC LIMIT 1""",
                    (mint, pair, target.isoformat(),
                     (target + timedelta(minutes=MAX_SAMPLE_LAG_MINUTES)).isoformat())
                ).fetchone()
                if row is None or not entry:
                    sample["horizons"][label] = {"status": "MISSING_MARKET_SAMPLE"}
                else:
                    price = valid_positive(row[1])
                    if price is None:
                        sample["horizons"][label] = {"status": "INVALID_MARKET_SAMPLE"}
                    else:
                        sample["horizons"][label] = {
                            "status": "OBSERVED", "sampled_at": row[0],
                            "price_change_pct": round((price / entry - 1) * 100, 4)}
            records.append(sample)
    summary = {}
    for group in sorted(set(r["decision"] for r in records)):
        selection = [r for r in records if r["decision"] == group]
        horizons = {}
        for minutes in HORIZONS_MINUTES:
            label = str(minutes)+"m"
            statuses = Counter(r["horizons"][label]["status"] for r in selection)
            observed = [r["horizons"][label]["price_change_pct"] for r in selection
                        if r["horizons"][label]["status"] == "OBSERVED"]
            horizons[label] = {
                "statuses": dict(statuses), "observed_count": len(observed),
                "mean_price_change_pct": round(sum(observed)/len(observed), 4) if observed else None,
                "positive_price_changes": sum(v > 0 for v in observed)}
        summary[group] = {"observations": len(selection), "horizons": horizons}
    return {
        "mode": "B3_FORWARD_PRICE_RESEARCH_ONLY",
        "generated_utc": now.isoformat(timespec="seconds"),
        "total_observations": len(records),
        "by_decision": summary,
        "examples": records[-MAX_EXAMPLES:],
        "limitations": [
            "Prices are Dexscreener snapshots, NOT Jupiter executable quotes or realized returns.",
            "Same pair address required; no value assigned to missing future prices.",
            "Selection bias, stale/missing data, and thin liquidity may distort comparisons.",
            "No fees, gas, spread, slippage, token security or execution estimates.",
            "Repeated snapshots of a token are correlated; not independent trades.",
            "Does not change the B3 state or any strategy."
        ],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(args.db, args.state)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name+".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"mode": result["mode"], "total": result["total_observations"],
                      "by_decision": result["by_decision"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
