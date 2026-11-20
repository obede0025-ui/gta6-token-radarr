"""V3.5 isolated current market-evidence observation. NO trading.

Reads existing V3.3 ledger and SQLite snapshots read-only. Bounded, fresh
candidate selection; contemporaneous read-only RPC and Jupiter quote-only GET
using the *historic hypothetical* buy amount (no new buy / no wallet).
Writes ONLY its own output JSON if --output is supplied. V3.4 remains intact.
A sell quote is not a filled or executable trade.
"""
import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from radar_token_security import DEFAULT_RPC, evaluate_mint, rpc_get_account
from research_preentry_probe_v31 import KeylessQuoteOnly, USDC
from research_token2022_risk_diagnostics import assess_token2022_risk

MAX_CANDIDATES = 3
MAX_AGE_SEC = 120


def utc(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def read_ledger(path):
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or not isinstance(state.get("signals"), dict)
            or state.get("paper_orders_executed") != 0
            or state.get("live_orders_executed") != 0):
        raise ValueError("Invalid research ledger")
    return state["signals"]


def select_fresh(db_path, ledger_path, now, limit=MAX_CANDIDATES):
    """Select distinct ledger mints with a <=120s SQLite snapshot."""
    if not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("Candidate limit must be 1..3")
    signals = read_ledger(ledger_path)
    by_mint = {}
    for key, entry in signals.items():
        if not isinstance(entry, dict):
            continue
        mint = entry.get("mint")
        raw = entry.get("buy_raw_out")
        if not isinstance(mint, str) or not (32 <= len(mint) <= 44):
            continue
        # Prefer the most recently admitted research signal for each mint.
        stamp = utc(entry.get("first_seen_utc"))
        if stamp is None or stamp > now:
            continue
        if mint not in by_mint or stamp > by_mint[mint][0]:
            by_mint[mint] = (stamp, raw)
    if not by_mint:
        return [], {"ledger_mints": 0, "fresh_mints": 0, "stale_or_missing_mints": 0}

    # Existing source DB remains read-only. No auxiliary tables or migrations.
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        mints = sorted(by_mint)
        placeholders = ",".join("?" for _ in mints)
        latest = {}
        for mint, stamp, liquidity in conn.execute(
                "SELECT token_address,scanned_at,liquidity_usd FROM snapshots "
                f"WHERE token_address IN ({placeholders}) ORDER BY scanned_at DESC",
                mints):
            if mint not in latest:
                latest[mint] = (stamp, liquidity)

    fresh = []
    for mint, (entry_stamp, buy_raw) in by_mint.items():
        observation = latest.get(mint)
        if observation is None:
            continue
        scanned_at, liquidity = observation
        dt = utc(scanned_at)
        if dt is None or not 0 <= (now - dt).total_seconds() <= MAX_AGE_SEC:
            continue
        fresh.append({"mint": mint, "snapshot_utc": scanned_at,
                      "liquidity_usd": liquidity, "buy_raw_out": buy_raw})
    fresh.sort(key=lambda x: (x["snapshot_utc"], x["mint"]), reverse=True)
    return fresh[:limit], {
        "ledger_mints": len(by_mint), "fresh_mints": len(fresh),
        "stale_or_missing_mints": len(by_mint) - len(fresh),
        "selected_mints": min(len(fresh), limit),
    }


def observe_candidate(candidate, rpc_session, quoter, clock, rpc_url=DEFAULT_RPC):
    """Get new mint authority evidence and a fresh hypothetical sell quote."""
    mint = candidate["mint"]
    stamp = lambda: clock().isoformat(timespec="seconds")
    rpc_error = None
    try:
        account = rpc_get_account(rpc_session, rpc_url, mint)
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        account = None
        rpc_error = type(error).__name__
    security = evaluate_mint(mint, ["v35_market_evidence"], account)
    security["checked_utc"] = stamp()

    raw = candidate.get("buy_raw_out")
    quote_status = "SKIPPED_NO_HYPOTHETICAL_AMOUNT"
    sell_out = None
    if isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
        try:
            sell_out = quoter.quote(mint, USDC, int(raw))
            quote_status = "QUOTE_ONLY_OK"
        except (requests.RequestException, ValueError, TypeError, OverflowError) as error:
            quote_status = "QUOTE_UNAVAILABLE_" + type(error).__name__
    # No quote (including 429/HTTP failure) is UNKNOWN, not a proven failed fill.
    observed_at = stamp()
    market = {"mint": mint, "checked_utc": observed_at,
              "liquidity_usd": candidate["liquidity_usd"],
              "sell_quote_available": True if quote_status == "QUOTE_ONLY_OK" else None}
    score = assess_token2022_risk(security, market)
    # Never allow a source snapshot to qualify as fresh merely because a slow
    # network call finished later; do not reuse market values if now too old.
    snap = utc(candidate["snapshot_utc"])
    now = clock()
    if snap is None or not 0 <= (now - snap).total_seconds() <= MAX_AGE_SEC:
        market["checked_utc"] = None
        market["liquidity_usd"] = None
        market["sell_quote_available"] = None
        score = assess_token2022_risk(security, market)
        quote_status = "MARKET_SNAPSHOT_STALE_AFTER_PROBE"

    return {
        "mint": mint,
        "snapshot_utc": candidate["snapshot_utc"],
        "rpc_status": "OK" if rpc_error is None and account is not None else "UNKNOWN",
        "rpc_error_type": rpc_error,
        "quote_status": quote_status,
        "hypothetical_token_raw": raw,
        "sell_quote_usdc_raw": str(sell_out) if sell_out is not None else None,
        "security_gate": security["security_gate"],
        "risk": score,
        "paper_trade_approval": "BLOCK",
        "live_trade_approval": "BLOCK",
    }


def cycle(db, ledger, output=None, limit=MAX_CANDIDATES, now=None,
          rpc_session=None, quoter=None, rpc_url=DEFAULT_RPC, clock=None):
    if not rpc_url.startswith("https://"):
        raise ValueError("HTTPS RPC required")
    db, ledger = Path(db).resolve(), Path(ledger).resolve()
    if not db.is_file() or not ledger.is_file():
        raise FileNotFoundError("Existing source DB and ledger required")
    if output is not None and Path(output).resolve() in (db, ledger):
        raise ValueError("Output cannot replace source database or ledger")
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    candidates, discovery = select_fresh(db, ledger, now, limit)

    owned_rpc = rpc_session is None
    owned_quoter = quoter is None
    rpc_session = rpc_session or requests.Session()
    quoter = quoter or KeylessQuoteOnly()
    try:
        rows = [observe_candidate(c, rpc_session, quoter, clock, rpc_url) for c in candidates]
    finally:
        if owned_rpc:
            rpc_session.close()
        if owned_quoter:
            quoter.close()

    report = {
        "mode": "V35_ISOLATED_MARKET_EVIDENCE_NO_TRADING",
        "checked_utc": clock().isoformat(timespec="seconds"),
        "selection": discovery,
        "results": rows,
        "paper_orders_executed": 0,
        "live_orders_executed": 0,
        "source_db_writes": 0,
        "source_ledger_writes": 0,
        "notes": [
            "Only fresh <=120 second DB snapshots can contribute liquidity evidence",
            "Jupiter GET quote uses historical hypothetical buy amount; no buy executed",
            "Quote availability does not prove a fill, LP lock, or holder safety",
            "Unavailable quotes remain UNKNOWN instead of being labeled risk-free",
            "All Token-2022 safety and trading gates remain blocked",
        ],
    }
    if output is not None:
        path = Path(output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name+".tmp-",
                                             encoding="utf-8", delete=False) as file:
                temporary = file.name
                json.dump(report, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", help="Dedicated V3.5 JSON output only")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(cycle(args.db, args.ledger, args.output, args.limit), indent=2))


if __name__ == "__main__":
    main()
