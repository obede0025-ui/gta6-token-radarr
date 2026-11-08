"""V6.3: read-only market/Jupiter crosscheck. Research flags, never trading commands."""
import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

def parse_utc(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None

def positive_number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def compare(quotes, db_path, *, now=None, max_quote_age=900, max_market_gap=900,
            minimum_liquidity=10000, ratio_low=Decimal("0.8"),
            ratio_high=Decimal("1.2")):
    now = now or datetime.now(timezone.utc)
    stamp = parse_utc(quotes.get("checked_utc"))
    quote_recent = (stamp is not None and
                    0 <= (now - stamp).total_seconds() <= max_quote_age)
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError("Snapshot database missing; audit aborted")
    result = []
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        for token in quotes.get("positions", []):
            mint = token.get("mint")
            if not isinstance(mint, str) or not mint:
                continue
            row = conn.execute(
                "SELECT scanned_at, price_usd, liquidity_usd, pair_address "
                "FROM snapshots WHERE token_address=? ORDER BY scanned_at DESC LIMIT 1",
                (mint,)
            ).fetchone()
            for position in token.get("positions") or []:
                risk = "UNKNOWN"
                detail = "NO_SNAPSHOT"
                ratio = None
                liquidity = None
                if not quote_recent:
                    detail = "STALE_QUOTE"
                elif row is None:
                    detail = "NO_SNAPSHOT"
                else:
                    observed, price, raw_liq, pair = row
                    observation_time = parse_utc(observed)
                    if observation_time is None or stamp is None or abs((stamp - observation_time).total_seconds()) > max_market_gap:
                        detail = "TIMESTAMP_MISMATCH"
                    else:
                        price_dec = positive_number(price)
                        quantity = positive_number(position.get("position_quantity"))
                        exit_value = positive_number(position.get("estimated_exit_usdc"))
                        try:
                            liquidity = float(raw_liq) if raw_liq is not None else None
                        except (TypeError, ValueError, OverflowError):
                            liquidity = None
                        if position.get("status") != "QUOTE_AVAILABLE":
                            detail = "QUOTE_UNAVAILABLE"
                        elif not price_dec or not quantity or not exit_value:
                            detail = "INVALID_PRICE_QUANTITY_OR_QUOTE"
                        else:
                            ratio = exit_value / (quantity * price_dec)
                            if not ratio.is_finite():
                                detail = "INVALID_RATIO"
                            elif liquidity is None or not math.isfinite(liquidity) or liquidity < minimum_liquidity:
                                detail = "LOW_OR_UNKNOWN_LIQUIDITY"
                                risk = "BLOCK_FOR_REVIEW"
                            elif ratio < ratio_low or ratio > ratio_high:
                                detail = "PRICE_DISAGREEMENT"
                                risk = "BLOCK_FOR_REVIEW"
                            else:
                                detail = "CONSISTENT_INDICATION"
                                risk = "PASS_RESEARCH_ONLY"
                result.append({
                    "mint": mint,
                    "strategy": position.get("strategy"),
                    "risk": risk,
                    "reason": detail,
                    "jupiter_to_market_ratio": round(float(ratio), 6) if ratio is not None else None,
                    "liquidity_usd": liquidity,
                })
    return {
        "mode": "MARKET_CROSSCHECK_V63_READ_ONLY",
        "quote_checked_utc": quotes.get("checked_utc"),
        "evaluated_utc": now.isoformat(timespec="seconds"),
        "positions": result,
        "counts": {reason: sum(x["reason"] == reason for x in result)
                   for reason in sorted({x["reason"] for x in result})},
        "warning": "Research classifications only. PASS does not establish safe or executable trading."
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotes", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-quote-age", type=int, default=900)
    parser.add_argument("--max-market-gap", type=int, default=900)
    args = parser.parse_args()
    if args.max_quote_age <= 0 or args.max_market_gap <= 0:
        parser.error("Ages must be positive")
    quotes = json.loads(Path(args.quotes).read_text(encoding="utf-8"))
    report = compare(quotes, args.db, max_quote_age=args.max_quote_age,
                     max_market_gap=args.max_market_gap)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)
    print(json.dumps(report["counts"]))

if __name__ == "__main__":
    main()
