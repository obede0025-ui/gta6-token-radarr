"""One-run, resumable historical SOL-flow comparison: V3.2 extreme losses vs wins.

Read-only RPC and SQLite. Writes a separate JSON research checkpoint only.
Classification: PumpSwap Buy/Sell log + token reserve balance direction.
SOL proceeds: positive *wallet* lamport change (approximate; may include rent/fees)
or WSOL wallet-account balance change. Unknown is NOT silently zero.
Never treats post-entry observations as a pre-entry predictor.
"""
import argparse
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from research_crash_flow_batch import POOLS, fetch, timestamp
from v32_quote_performance import evaluate

WSOL = "So11111111111111111111111111111111111111112"


def balance_changes(meta, mint):
    accounts = {}
    for field, ix in (("preTokenBalances", 0), ("postTokenBalances", 1)):
        for item in meta.get(field, []) or []:
            if item.get("mint") != mint:
                continue
            key = (item.get("accountIndex"), item.get("owner"))
            raw = (item.get("uiTokenAmount") or {}).get("uiAmountString")
            if raw is not None:
                accounts.setdefault(key, [0.0, 0.0])[ix] = float(raw)
    return [(owner, post - pre) for (_, owner), (pre, post) in accounts.items()]


def parse_trade(tx, mint, pool):
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return {"side": "FAILED"}
    logs = meta.get("logMessages") or []
    sells = any("Instruction: Sell" in x for x in logs)
    buys = any("Instruction: Buy" in x for x in logs)
    side = "SELL" if sells and not buys else ("BUY" if buys and not sells
           else "MIXED" if sells and buys else "OTHER")
    mint_changes = balance_changes(meta, mint)
    pool_changes = [delta for owner, delta in mint_changes if owner == pool]
    pool_token = sum(pool_changes) if pool_changes else None
    # For a single-leg swap, token amount entering/leaving pool is interpretable;
    # transactions with multiple swaps remain ambiguous and are excluded.
    token_amount = None
    if side == "SELL" and pool_token is not None and pool_token > 0:
        token_amount = pool_token
    if side == "BUY" and pool_token is not None and pool_token < 0:
        token_amount = -pool_token

    owners = {
        owner for owner, delta in mint_changes
        if owner and owner != pool
        and ((side == "SELL" and delta < 0) or (side == "BUY" and delta > 0))
    }
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    lookup = {k.get("pubkey"): i for i, k in enumerate(keys)
              if isinstance(k, dict)}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    native = []
    for owner in owners:
        idx = lookup.get(owner)
        if idx is not None and idx < len(pre) and idx < len(post):
            native.append((owner, (post[idx] - pre[idx]) / 1e9))
    native_positive = [(owner, delta) for owner, delta in native if delta > 0]
    native_negative = [(owner, -delta) for owner, delta in native if delta < 0]

    wsol_changes = balance_changes(meta, WSOL)
    wsol_wallet_positive = [(owner, delta) for owner, delta in wsol_changes
                            if owner in owners and delta > 0]
    wsol_wallet_negative = [(owner, -delta) for owner, delta in wsol_changes
                            if owner in owners and delta < 0]
    # If seller's wallet has a positive WSOL flow, prefer it over native
    # lamports to avoid double counting. Otherwise native wallet delta is an
    # approximation (fees, rent, bundled instructions may be included).
    if side == "SELL" and token_amount is not None:
        if wsol_wallet_positive:
            sol = sum(x[1] for x in wsol_wallet_positive)
            method = "WALLET_WSOL_DELTA"
        elif native_positive:
            sol = sum(x[1] for x in native_positive)
            method = "WALLET_SOL_NET_APPROX"
        else:
            sol, method = None, "UNKNOWN"
    elif side == "BUY" and token_amount is not None:
        if wsol_wallet_negative:
            sol = sum(x[1] for x in wsol_wallet_negative)
            method = "WALLET_WSOL_DELTA"
        elif native_negative:
            sol = sum(x[1] for x in native_negative)
            method = "WALLET_SOL_NET_APPROX"
        else:
            sol, method = None, "UNKNOWN"
    else:
        sol, method = None, "UNKNOWN"
    lp_hints = [x.strip()[:120] for x in logs
                if "Instruction:" in x and any(k in x.lower()
                for k in ("remove_liquidity", "withdraw", "decrease_liquidity"))]
    return {
        "side": side, "tokens": token_amount, "sol": sol,
        "sol_method": method, "wallets": sorted(owners),
        "lp_hints": lp_hints,
    }


def load_json(path, default):
    if not Path(path).exists():
        return default
    return json.loads(Path(path).read_text())


def save_atomic(path, obj):
    p = Path(path)
    temp = p.with_name(p.name + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    temp.replace(p)


def query_pair(conn, mint, signal):
    return conn.execute("""
        SELECT pair_address, liquidity_usd, volume_m5
        FROM snapshots WHERE token_address=?
          AND datetime(scanned_at)<=datetime(?)
        ORDER BY datetime(scanned_at) DESC LIMIT 1
    """, (mint, signal)).fetchone()


def select_cohorts(state, conn, existing, max_wins):
    positions = state["positions"]
    selected = []
    for prefix, pool in POOLS.items():
        items = [(mint, p) for mint, p in positions.items()
                 if mint.startswith(prefix) and p.get("entry_quote_utc")]
        if len(items) == 1:
            mint, p = items[0]
            selected.append(("EXTREME", mint, pool, p))
    old_wins = [r for r in existing.get("results", []) if
                r.get("cohort") == "WIN" and r.get("history_complete")]
    priority = [r["mint"] for r in old_wins]
    candidates = sorted(
        (mint for mint, p in positions.items()
         if p.get("entry_quote_utc")
         and evaluate(p, tp=10, sl=-10, horizon=900, cost_pp=2)["outcome"]
         == "TP_OBSERVED"))
    ordered = list(dict.fromkeys(priority + candidates))
    for mint in ordered:
        p = positions.get(mint)
        if not p:
            continue
        row = query_pair(conn, mint, p["signal_utc"])
        if row and row[0]:
            selected.append(("WIN", mint, row[0], p))
        if sum(c[0] == "WIN" for c in selected) >= max_wins:
            break
    return selected


def fetch_window(session, rpc, pool, start, end, max_pages):
    sigs, cursor, complete, error = [], None, False, None
    for _ in range(max_pages):
        cfg = {"limit": 1000, "commitment": "finalized"}
        if cursor:
            cfg["before"] = cursor
        try:
            batch = fetch(session, rpc, "getSignaturesForAddress", [pool, cfg])
        except Exception as exc:
            error = str(exc)
            break
        if not batch:
            error = "HISTORY_EXHAUSTED"
            break
        cursor = batch[-1]["signature"]
        sigs.extend(x for x in batch if
                    x.get("blockTime") is not None and start <= x["blockTime"] <= end)
        ts = [x["blockTime"] for x in batch if x.get("blockTime") is not None]
        if ts and min(ts) < start:
            complete = True
            break
    return sigs, complete, error


def summarize(rows):
    out = {}
    for label in ("BEFORE", "ENTRY_SECOND_UNORDERED", "AFTER"):
        group = [x for x in rows if x["period"] == label]
        counts = Counter(x["side"] for x in group)
        sells = [x for x in group if x["side"] == "SELL"]
        buys = [x for x in group if x["side"] == "BUY"]
        sold_sol = [x["sol"] for x in sells if x.get("sol") is not None]
        bought_sol = [x["sol"] for x in buys if x.get("sol") is not None]
        wallet_sales = defaultdict(float)
        for x in sells:
            if x.get("sol") is not None:
                for wallet in x.get("wallets") or []:
                    wallet_sales[wallet] += x["sol"] / len(x["wallets"])
        out[label] = {
            "decoded": len(group), "counts": dict(counts),
            "sell_share_count_pct": round(100 * len(sells) / (len(sells) + len(buys)), 2)
            if sells or buys else None,
            "sell_sol_known_sum": round(sum(sold_sol), 8),
            "buy_sol_known_sum": round(sum(bought_sol), 8),
            "sell_sol_known_n": len(sold_sol),
            "sell_sol_unknown_n": len(sells) - len(sold_sol),
            "buy_sol_known_n": len(bought_sol),
            "buy_sol_unknown_n": len(buys) - len(bought_sol),
            "sell_tokens_known_sum": round(sum(x["tokens"] for x in sells
                                               if x.get("tokens") is not None), 2),
            "top_sellers_by_known_sol": [
                {"wallet": w, "sol_approx": round(v, 8)}
                for w, v in sorted(wallet_sales.items(),
                                   key=lambda x: x[1], reverse=True)[:5]],
            "largest_known_sales": sorted([
                {"utc": x["utc"], "signature": x["signature"],
                 "sol_approx": round(x["sol"], 8),
                 "method": x["sol_method"]}
                for x in sells if x.get("sol") is not None
            ], key=lambda x: x["sol_approx"], reverse=True)[:5],
            "lp_hints_count": sum(bool(x.get("lp_hints")) for x in group),
        }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    ap.add_argument("--db", default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    ap.add_argument("--prior", default="research_crash_flow_with_winners.json")
    ap.add_argument("--output", default="research_sol_flow_detailed.json")
    ap.add_argument("--rpc", default="https://solana-rpc.publicnode.com")
    ap.add_argument("--wins", type=int, default=6)
    ap.add_argument("--before", type=int, default=40)
    ap.add_argument("--after", type=int, default=40)
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--max-tx-per-period", type=int, default=1200)
    ap.add_argument("--delay", type=float, default=0.08)
    args = ap.parse_args()
    if not args.rpc.startswith("https://"):
        ap.error("RPC URL must use HTTPS")
    if not 1 <= args.wins <= 28 or not 10 <= args.before <= 300 or not 10 <= args.after <= 300:
        ap.error("invalid limits")
    if not 1 <= args.max_pages <= 50 or not 20 <= args.max_tx_per_period <= 5000:
        ap.error("invalid workload limits")
    state = load_json(args.state, {})
    prior = load_json(args.prior, {"results": []})
    report = load_json(args.output, {"mode": "READ_ONLY_HISTORICAL_SOL_FLOW",
                                    "results": {}})
    with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        cohorts = select_cohorts(state, conn, prior, args.wins)
        with requests.Session() as session:
            for cohort, mint, pool, pos in cohorts:
                entry = timestamp(pos["entry_quote_utc"])
                start = int(entry.timestamp()) - args.before
                middle = int(entry.timestamp())
                end = middle + args.after
                key = mint
                saved = report["results"].get(key, {})
                # Strict identity and window parameters for safe resumption.
                params = {"entry_utc": entry.isoformat(), "pool": pool,
                          "before": args.before, "after": args.after,
                          "limit": args.max_tx_per_period}
                if saved.get("params") != params:
                    saved = {"cohort": cohort, "params": params, "transactions": {},
                             "complete": False}
                if saved.get("complete"):
                    print(mint[:12], "already complete; reusing", flush=True)
                    continue
                if "signatures" not in saved:
                    sigs, complete, error = fetch_window(
                        session, args.rpc, pool, start, end, args.max_pages)
                    saved["history_complete"] = complete
                    saved["history_error"] = error
                    def tag(x):
                        t = x["blockTime"]
                        return ("BEFORE" if t < middle
                                else "AFTER" if t > middle else "ENTRY_SECOND_UNORDERED")
                    sampled = []
                    for label in ("BEFORE", "ENTRY_SECOND_UNORDERED", "AFTER"):
                        batch = sorted((x for x in sigs if tag(x) == label),
                                       key=lambda x: (x["blockTime"], x["signature"]))
                        saved.setdefault("counts_all", {})[label] = len(batch)
                        limit = args.max_tx_per_period
                        if len(batch) > limit:
                            # Uniform deterministic sampling; not total volume.
                            batch = [batch[(i * (len(batch)-1)) // (limit-1)]
                                     for i in range(limit)]
                        sampled += [{"signature": x["signature"], "utc": x["blockTime"],
                                     "period": label} for x in batch]
                    saved["signatures"] = sampled
                    report["results"][key] = saved
                    save_atomic(args.output, report)
                print(f"\n{cohort} {mint[:12]} | signatures={len(saved['signatures'])} "
                      f"| history={saved.get('history_complete')}", flush=True)
                for n, item in enumerate(saved["signatures"], 1):
                    sig = item["signature"]
                    if sig in saved["transactions"]:
                        continue
                    try:
                        tx = fetch(session, args.rpc, "getTransaction", [
                            sig, {"encoding": "jsonParsed",
                                  "maxSupportedTransactionVersion": 1,
                                  "commitment": "finalized"}])
                        if not tx:
                            rec = {"side": "UNAVAILABLE"}
                        else:
                            rec = parse_trade(tx, mint, pool)
                        saved["transactions"][sig] = {
                            "utc": datetime.fromtimestamp(item["utc"], timezone.utc).isoformat(),
                            "signature": sig, "period": item["period"], **rec,
                        }
                    except Exception as exc:
                        saved.setdefault("errors", {})[sig] = type(exc).__name__
                    if n % 25 == 0:
                        report["results"][key] = saved
                        save_atomic(args.output, report)
                        print(f"  {n}/{len(saved['signatures'])}, "
                              f"decoded={len(saved['transactions'])}", flush=True)
                    time.sleep(args.delay)
                saved["summary"] = summarize(list(saved["transactions"].values()))
                saved["complete"] = (saved.get("history_complete", False)
                                     and len(saved["transactions"]) == len(saved["signatures"]))
                report["results"][key] = saved
                save_atomic(args.output, report)
                pre = saved["summary"]["BEFORE"]
                print("  BEFORE sell/buy:", pre["counts"].get("SELL", 0),
                      "/", pre["counts"].get("BUY", 0),
                      "| SOL sell known:", pre["sell_sol_known_sum"],
                      "| SOL sell unknown tx:", pre["sell_sol_unknown_n"],
                      "| complete:", saved["complete"], flush=True)

    print("\n=== COHORT COMPARISON, PRE-ENTRY ONLY ===")
    for cohort in ("EXTREME", "WIN"):
        rows = [(mint[:12], rec) for mint, rec in report["results"].items()
                if rec.get("cohort") == cohort and rec.get("history_complete")
                and rec.get("summary")]
        print(cohort, "tokens_with_history=", len(rows))
        for mint, item in rows:
            pre = item["summary"]["BEFORE"]
            print(mint, "sell_pct=", pre["sell_share_count_pct"],
                  "sell_SOL_known=", pre["sell_sol_known_sum"],
                  "sell_unknown=", pre["sell_sol_unknown_n"],
                  "buy_SOL_known=", pre["buy_sol_known_sum"],
                  "lp_hints=", pre["lp_hints_count"],
                  "decoded=", pre["decoded"],
                  "total_before=", item["counts_all"].get("BEFORE"))
    print("Report:", args.output)
    print("Note: SOL wallet balance changes are approximate; incomplete coverage"
          " and unknown values prohibit definitive profit or rug-pull conclusions.")


if __name__ == "__main__":
    main()
