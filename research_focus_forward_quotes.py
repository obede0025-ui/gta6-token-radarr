"""Forward-only A/B2 focus quote cohort. Manual one-shot; no auto scheduler.

New signals are selected prospectively from existing snapshots with <=120s
freshness. Jupiter GET-only buy and sell quotes for hypothetical $10; then
1m/5m/15m sell re-quotes of the fixed imaginary token quantity. Does not
buy, execute, authorize paper entries or modify historic state or source DB.
"""
import argparse
import fcntl
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from research_focus_a_b2 import diagnose_and_shortlist, timestamp
from research_preentry_probe_v31 import KeylessQuoteOnly, USDC, BUY_USDC_RAW

HORIZONS = (60, 300, 900)
GRACE = 60
MAX_NEW = 3
MAX_FOLLOWUPS = 6
MAX_TRACKED = 200


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8",
                                         prefix=path.name+".tmp-", delete=False) as f:
            temp = f.name
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def load_state(path, now):
    if not path.exists():
        return {"version": 1, "started_utc": now.isoformat(timespec="seconds"),
                "signals": {}, "paper_orders_executed": 0, "live_orders_executed": 0}
    state = json.loads(path.read_text())
    if (state.get("version") != 1 or not isinstance(state.get("signals"), dict)
            or state.get("paper_orders_executed") != 0
            or state.get("live_orders_executed") != 0):
        raise ValueError("Invalid isolated focus cohort; refusing to overwrite")
    return state


def _quote(quoter, input_mint, output_mint, amount):
    try:
        return quoter.quote(input_mint, output_mint, amount)
    except (requests.RequestException, ValueError, TypeError, OverflowError):
        return None


def run(db_path, state_path, now=None, quoter=None, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError("UTC-aware clock required")
    db_path = Path(db_path).resolve()
    state_path = Path(state_path).resolve()
    if not db_path.is_file() or db_path == state_path:
        raise ValueError("Source DB required; state must have a different path")
    if state_path.name != "forward_quotes.json" or state_path.parent.name != "focus":
        raise ValueError("Isolated state must be focus/forward_quotes.json; refusing other output paths")
    # A process-level lock prevents lost JSON updates from overlapping one-shots.
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_state(state_path, now)
        with sqlite3.connect(db_path.as_uri()+"?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            selected = diagnose_and_shortlist(conn, now)["fresh_shortlist"]
        new_count = 0
        outcomes = []
        owned = quoter is None
        quoter = quoter or KeylessQuoteOnly()
        try:
            # Existing historical data never creates a retrospective fill:
            # selection is always within 120 seconds of this exact run.
            for item in selected:
                key = item["mint"] + "|" + item["pair"]
                if key in state["signals"] or len(state["signals"]) >= MAX_TRACKED:
                    continue
                # Quotes may lag while processing earlier selected mints.
                observed = timestamp(item["observed_utc"])
                current = clock()
                # Recheck freshness after any preceding network requests.
                if observed is None or not 0 <= (current-observed).total_seconds() <= 120:
                    continue
                mint = item["mint"]
                buy = _quote(quoter, USDC, mint, BUY_USDC_RAW)
                sell = _quote(quoter, mint, USDC, buy) if buy is not None else None
                state["signals"][key] = {
                    "mint": mint, "pair": item["pair"], "strategies": item["strategies"],
                    "signal_utc": item["observed_utc"],
                    "first_quote_utc": clock().isoformat(timespec="seconds"),
                    "initial_buy_raw": str(buy) if buy is not None else None,
                    "initial_sell_usdc_raw": str(sell) if sell is not None else None,
                    "entry_quote_status": "BUY_AND_SELL_QUOTED" if sell is not None
                        else "BUY_ONLY" if buy is not None else "NO_BUY_ROUTE",
                    "followups": {},
                    "live_trade_approval": "BLOCK", "paper_trade_approval": "BLOCK",
                }
                new_count += 1

            # Follow-ups only count if requested within their own due-time window.
            due = []
            for key, item in state["signals"].items():
                anchor = timestamp(item.get("first_quote_utc"))
                if anchor is None or anchor > now:
                    continue
                for horizon in HORIZONS:
                    if str(horizon) not in item.get("followups", {}) and (now-anchor).total_seconds() >= horizon:
                        due.append((anchor, key, horizon))
            for anchor, key, horizon in sorted(due)[:MAX_FOLLOWUPS]:
                item = state["signals"][key]
                age = (now-anchor).total_seconds()
                raw = item.get("initial_buy_raw")
                if age > horizon+GRACE:
                    status = "MISSED_HORIZON"
                    quote = None
                elif not (isinstance(raw, str) and raw.isdigit() and int(raw) > 0):
                    status = "NO_HYPOTHETICAL_ENTRY_AMOUNT"
                    quote = None
                else:
                    quote = _quote(quoter, item["mint"], USDC, int(raw))
                    status = "SELL_QUOTE_ONLY" if quote is not None else "QUOTE_UNAVAILABLE"
                item["followups"][str(horizon)] = {
                    "status": status,
                    "observed_utc": now.isoformat(timespec="seconds"),
                    "elapsed_seconds": round(age, 2),
                    "sell_usdc_raw": str(quote) if quote is not None else None,
                }
                outcomes.append({"mint":item["mint"],"horizon":horizon,"status":status})
        finally:
            if owned:
                quoter.close()
        _atomic(state_path, state)
    return {
        "mode": "A_B2_FORWARD_QUOTE_ONLY_NO_TRADING",
        "checked_utc": now.isoformat(timespec="seconds"),
        "new_signals": new_count, "total_signals": len(state["signals"]),
        "followups": outcomes,
        "paper_orders_executed": 0, "live_orders_executed": 0,
        "source_db_writes": 0, "source_portfolio_writes": 0,
        "limitations": [
            "Entry tokens are hypothetical; no fill, holder/LP or security authorization",
            "Jupiter GET quotes are indicative and do not include all network/priority costs",
            "Missed/unavailable quotes are not synthetic profitable exits",
            "Manual cycles require external scheduling if continuous monitoring is wanted",
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--state", required=True, help="New isolated focus JSON, never source portfolio")
    args = p.parse_args()
    print(json.dumps(run(args.db, args.state), indent=2))


if __name__ == "__main__":
    main()
