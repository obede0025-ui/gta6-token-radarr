"""Read-only historical Solana crash-flow audit for four V3.2 extreme-loss mints.

Uses finalized pool transaction signatures and parsed historical transactions.
Reports buys/sells in a sample of transactions, token flows, and LP log hints.
No orders, secrets, database writes, or timer changes. NOT proof of causation.
"""
import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from v32_quote_performance import evaluate as quote_evaluate

RPC_DEFAULT = "https://solana-rpc.publicnode.com"
POOLS = {
    "GPzFQboRBoTi": "7fwKwdigpcVS9vGSSXFxT3Zcg3syhMhaibxjCshnNR3t",
    "DdDLnJWEo2Tq": "GWL19R1oKrvokX9Tea2KzvPsPFFJqC8AEc2Dr6h2wyqk",
    "MbEJrnhhdWUg": "AZZYWxzhuBgMPGMmx9TeS3rEjKrMj23DaotMy7EEtR2",
    "3Ufq2s3yLx6T": "2Q1BzemocMGLH5VHvxoKczdgY2qUCC1StwDTHuwQM1cL",
}


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Expected UTC timestamp in V3.2 state")
    return dt.astimezone(timezone.utc)


def fetch(session, endpoint, method, params):
    last = None
    for retry in range(4):
        try:
            response = session.post(endpoint, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }, timeout=30)
            if response.status_code in (429, 503):
                last = f"HTTP_{response.status_code}"
                time.sleep(1.5 * (retry + 1))
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                error = data["error"]
                last = f"RPC_{error.get('code', 'UNKNOWN')}"
                if error.get("code") in (-32019, -32005):
                    time.sleep(1.5 * (retry + 1))
                    continue
                raise RuntimeError(last)
            return data["result"]
        except requests.RequestException as exc:
            last = type(exc).__name__
            time.sleep(retry + 1)
    raise RuntimeError(last or "RPC_UNAVAILABLE")


def sample_spread(items, limit):
    if len(items) <= limit:
        return items
    # Deterministic time-spread, without selecting for observed outcome.
    return [items[(i * (len(items) - 1)) // (limit - 1)]
            for i in range(limit)]


def token_delta(meta, mint, pool):
    """Sum token balance changes owned by the pool; None if none were recorded."""
    balances = {}
    for field, index in (("preTokenBalances", 0), ("postTokenBalances", 1)):
        for entry in meta.get(field, []) or []:
            if entry.get("mint") != mint or entry.get("owner") != pool:
                continue
            key = entry.get("accountIndex")
            pair = balances.setdefault(key, [0.0, 0.0])
            raw = (entry.get("uiTokenAmount") or {}).get("uiAmountString")
            if raw is None:
                continue
            pair[index] += float(raw)
    return sum(post - pre for pre, post in balances.values()) if balances else None


def signer_addresses(tx):
    keys = (tx.get("transaction") or {}).get("message", {}).get("accountKeys") or []
    return [item.get("pubkey") for item in keys
            if isinstance(item, dict) and item.get("signer")]


def summarize(details, mint, pool):
    meta = details.get("meta") or {}
    if meta.get("err") is not None:
        return {"kind": "FAILED"}
    logs = meta.get("logMessages") or []
    sells = sum("Instruction: Sell" in line for line in logs)
    buys = sum("Instruction: Buy" in line for line in logs)
    if sells and not buys:
        kind = "SELL"
    elif buys and not sells:
        kind = "BUY"
    elif sells and buys:
        kind = "MIXED"
    else:
        kind = "OTHER"
    # Hints, not validated LP withdrawal classification.
    lp_hints = [line.strip()[:140] for line in logs
                if ("Instruction:" in line and
                    any(word in line.lower() for word in
                        ("remove_liquidity", "withdraw", "decrease_liquidity", "burn")))]
    return {
        "kind": kind, "pool_token_delta": token_delta(meta, mint, pool),
        "signers": signer_addresses(details), "lp_hints": lp_hints,
    }


def audit_one(session, endpoint, prefix, mint, pool, position, args):
    entry = timestamp(position["entry_quote_utc"])
    start = int((entry - timedelta(seconds=args.before)).timestamp())
    middle = int(entry.timestamp())
    end = int((entry + timedelta(seconds=args.after)).timestamp())
    signatures, before, complete = [], None, False
    error = None
    for page in range(args.max_pages):
        cfg = {"limit": 1000, "commitment": "finalized"}
        if before:
            cfg["before"] = before
        try:
            batch = fetch(session, endpoint, "getSignaturesForAddress", [pool, cfg])
        except Exception as exc:
            error = str(exc)
            break
        if not batch:
            error = "HISTORY_EXHAUSTED"
            break
        before = batch[-1]["signature"]
        signatures.extend(
            item for item in batch
            if item.get("blockTime") is not None
            and start <= item["blockTime"] <= end
        )
        stamps = [item["blockTime"] for item in batch if item.get("blockTime") is not None]
        if stamps and min(stamps) < start:
            complete = True
            break

    signatures.sort(key=lambda tx: (tx["blockTime"], tx["signature"]))
    # UTC seconds are not sufficient to order trades at the exact entry second.
    pre = [tx for tx in signatures if tx["blockTime"] < middle]
    same = [tx for tx in signatures if tx["blockTime"] == middle]
    post = [tx for tx in signatures if tx["blockTime"] > middle]
    windows = {
        "BEFORE": pre, "ENTRY_SECOND_UNORDERED": same, "AFTER": post,
    }
    out = {
        "mint": mint, "pool": pool, "entry_utc": entry.isoformat(),
        "window_seconds": {"before": args.before, "after": args.after},
        "history_complete": complete, "history_error": error,
        "total_signatures": len(signatures), "windows": {},
    }
    print(f"\n{prefix} | signatures={len(signatures)} | history_complete={complete}", flush=True)
    for label, items in windows.items():
        selected = sample_spread(items, args.max_sample)
        counts = Counter()
        sells = []
        buy_tokens = sell_tokens = 0.0
        verified_flows = 0
        lp_hints = []
        for k, record in enumerate(selected, 1):
            try:
                tx = fetch(session, endpoint, "getTransaction", [
                    record["signature"], {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 1,
                        "commitment": "finalized",
                    }
                ])
                if not tx:
                    counts["UNAVAILABLE"] += 1
                    continue
                parsed = summarize(tx, mint, pool)
                kind = parsed["kind"]
                counts[kind] += 1
                delta = parsed.get("pool_token_delta")
                if delta is not None:
                    verified_flows += 1
                    if kind == "SELL" and delta > 0:
                        sell_tokens += delta
                        sells.append({
                            "utc": datetime.fromtimestamp(record["blockTime"], timezone.utc).isoformat(),
                            "signature": record["signature"],
                            "tokens_to_pool": round(delta, 4),
                            "signers": parsed["signers"][:5],
                        })
                    elif kind == "BUY" and delta < 0:
                        buy_tokens += -delta
                for item in parsed.get("lp_hints", []):
                    lp_hints.append({"signature": record["signature"], "log": item})
            except Exception as exc:
                counts["ERROR"] += 1
                if counts["ERROR"] <= 3:
                    print(f"  {label}: transaction error {type(exc).__name__}", flush=True)
            time.sleep(args.delay)
        out["windows"][label] = {
            "signature_count": len(items), "sample_count": len(selected),
            "counts": dict(counts), "token_flows_measured": verified_flows,
            "sampled_sell_tokens": round(sell_tokens, 4),
            "sampled_buy_tokens": round(buy_tokens, 4),
            "largest_sampled_sells": sorted(
                sells, key=lambda x: x["tokens_to_pool"], reverse=True
            )[:8],
            "lp_instruction_hints": lp_hints[:10],
            "sampling_note": "Sums cover sampled transactions only, NOT total volume."
        }
        print(
            f"  {label}: n={len(items)} sampled={len(selected)} "
            f"buy={counts['BUY']} sell={counts['SELL']} "
            f"sell_tokens_sample={sell_tokens:.0f} "
            f"buy_tokens_sample={buy_tokens:.0f} errors={counts['ERROR']}",
            flush=True,
        )
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    parser.add_argument("--rpc", default=RPC_DEFAULT)
    parser.add_argument("--output", default="research_crash_flow_with_winners.json")
    parser.add_argument("--before", type=int, default=40)
    parser.add_argument("--after", type=int, default=40)
    parser.add_argument("--max-sample", type=int, default=60)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.12)
    parser.add_argument("--include-winners", type=int, default=6,
                        help="Also audit first N deterministic positive quote scenarios")
    parser.add_argument("--db", default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    args = parser.parse_args()
    if not 5 <= args.before <= 300 or not 5 <= args.after <= 300:
        parser.error("before/after must be between 5 and 300 seconds")
    if not 2 <= args.max_sample <= 200 or not 1 <= args.max_pages <= 30:
        parser.error("sample or page limit invalid")
    if not args.rpc.startswith("https://"):
        parser.error("RPC must be HTTPS")
    if not 0 <= args.include_winners <= 28:
        parser.error("include-winners must be 0..28")
    positions = json.loads(Path(args.state).read_text())["positions"]
    results = []
    cohorts = []
    if args.include_winners:
        # Only pair snapshots known AT OR BEFORE the signal; never use later pool selection.
        with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
            positive = sorted(
                ((mint, p) for mint, p in positions.items()
                 if p.get("entry_quote_utc")
                 and quote_evaluate(p, tp=10, sl=-10, horizon=900, cost_pp=2)["outcome"]
                 == "TP_OBSERVED"), key=lambda x: x[0])
            for mint, p in positive:
                if len(cohorts) >= args.include_winners:
                    break
                row = conn.execute("""
                    SELECT pair_address FROM snapshots
                    WHERE token_address = ?
                      AND datetime(scanned_at) <= datetime(?)
                    ORDER BY datetime(scanned_at) DESC LIMIT 1
                """, (mint, p["signal_utc"])).fetchone()
                if row and row[0]:
                    cohorts.append((mint, row[0], p))
    with requests.Session() as session:
        for prefix, pool in POOLS.items():
            matches = [(mint, p) for mint, p in positions.items() if mint.startswith(prefix)]
            if len(matches) != 1 or not matches[0][1].get("entry_quote_utc"):
                print(f"{prefix}: no unique quoted position; skipping")
                continue
            mint, pos = matches[0]
            audit = audit_one(session, args.rpc, prefix, mint, pool, pos, args)
            audit["cohort"] = "EXTREME"
            results.append(audit)
        for mint, pool, pos in cohorts:
            audit = audit_one(session, args.rpc, mint[:12], mint, pool, pos, args)
            audit["cohort"] = "WIN"
            results.append(audit)
    report = {
        "mode": "READ_ONLY_SAMPLED_HISTORICAL_CRASH_FLOW",
        "limitations": [
            "Transaction sample does not prove all pool trades were analyzed.",
            "Sell/Buy classification uses instructions, pool-owned token balances for sizing.",
            "LP instruction hints are NOT proof of liquidity removal.",
            "RPC blockTime seconds cannot establish intra-second ordering.",
            "On-chain transactions after entry are not valid pre-entry signals.",
            "Winning controls selected from already observed TP quote scenarios: selection bias.",
            "Token amounts are not SOL trade value; native/WSOL flows not reliably inferred.",
        ],
        "results": results,
    }
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nReport written: {dest} (only new report file; existing scanner untouched)")
    print("Summary: ", json.dumps([
        {"mint": x["mint"][:12], "cohort": x["cohort"], "complete": x["history_complete"],
         "pre": x["windows"]["BEFORE"]["counts"],
         "post": x["windows"]["AFTER"]["counts"]}
        for x in results], ensure_ascii=False))


if __name__ == "__main__":
    main()
