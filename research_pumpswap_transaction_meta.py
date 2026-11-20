"""Research-only PumpSwap transaction meta around historical zero observations.

Uses publicly available finalized Solana transaction metadata. Extracts token
balance deltas by account index without claiming these are pool vault reserves.
Never signs, broadcasts, writes a DB, or executes an order.
"""
import argparse
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from research_public_solana_pool_probe import CASES, rpc
import requests


def balance_map(transaction, which):
    """Return raw integer balance & decimals keyed by account index and mint."""
    meta = (transaction or {}).get("meta") or {}
    balances = {}
    for item in meta.get(which) or []:
        idx, mint = item.get("accountIndex"), item.get("mint")
        amt = (item.get("uiTokenAmount") or {}).get("amount")
        decimals = (item.get("uiTokenAmount") or {}).get("decimals")
        if not isinstance(idx, int) or not isinstance(mint, str):
            continue
        try:
            raw = int(amt)
            dec = int(decimals)
            if raw < 0 or not 0 <= dec <= 18:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        balances[idx, mint] = (raw, dec, item.get("owner"))
    return balances


def account_keys(transaction):
    message = (((transaction or {}).get("transaction") or {})
               .get("message") or {})
    keys = message.get("accountKeys") or []
    return [key.get("pubkey") if isinstance(key, dict) else key
            for key in keys]


def tx_summary(signature, transaction):
    """Token account deltas are transaction-level evidence, NOT pool reserves."""
    if not transaction:
        return {"signature":signature,"available":False,
                "token_account_deltas":[]}
    before = balance_map(transaction, "preTokenBalances")
    after = balance_map(transaction, "postTokenBalances")
    keys = account_keys(transaction)
    changes = []
    for idx, mint in sorted(set(before) | set(after)):
        p = before.get((idx, mint))
        q = after.get((idx, mint))
        if p is None or q is None or p[1] != q[1]:
            # Missing token balance metadata must NOT be read as zero reserve.
            continue
        delta = q[0] - p[0]
        if delta:
            changes.append({
                "token_account":keys[idx] if idx < len(keys) else None,
                "mint":mint,"decimals":p[1],
                "pre_raw":str(p[0]),"post_raw":str(q[0]),
                "delta_raw":str(delta),
                "owner_reported":q[2] or p[2],
                "verified_as_pumpswap_pool_vault":False
            })
    return {
        "signature":signature,
        "available":True,
        "slot":transaction.get("slot"),
        "block_time":transaction.get("blockTime"),
        "transaction_error":((transaction.get("meta") or {}).get("err")),
        "token_account_deltas":changes,
        "historical_pool_reserves_verified":False
    }


def select_signatures(signatures, target, each_side=2):
    before, after = [], []
    for item in signatures:
        time = item.get("blockTime")
        sig = item.get("signature")
        if not isinstance(time, int) or not sig or abs(time-target)>900:
            continue
        if time <= target:
            before.append(item)
        else:
            after.append(item)
    before.sort(key=lambda s:(target-s["blockTime"],s["signature"]))
    after.sort(key=lambda s:(s["blockTime"]-target,s["signature"]))
    return (before[:each_side]+after[:each_side])


def inspect(endpoint, session=None, each_side=2):
    session = session or requests.Session()
    cases = []
    for case in CASES:
        target = int(datetime.fromisoformat(case["zero_utc"]).timestamp())
        item = {
            "case":case["label"], "mint":case["mint"],"pool":case["pair"],
            "zero_utc":case["zero_utc"], "result":"UNVERIFIED",
            "historical_pool_reserves_verified":False,
            "transactions":[]
        }
        try:
            signatures = rpc(session, endpoint, "getSignaturesForAddress",
                             [case["pair"],{"limit":1000}]) or []
            selected = select_signatures(signatures, target, each_side)
            item["selected_signatures"] = len(selected)
            for hit in selected:
                try:
                    tx = rpc(session, endpoint, "getTransaction", [
                        hit["signature"],
                        {"encoding":"jsonParsed",
                         "commitment":"finalized",
                         "maxSupportedTransactionVersion":0}
                    ])
                    result = tx_summary(hit["signature"],tx)
                    result["seconds_from_zero"] = hit["blockTime"]-target
                    item["transactions"].append(result)
                except (requests.RequestException, RuntimeError, ValueError,
                        KeyError, TypeError) as exc:
                    item["transactions"].append({
                        "signature":hit["signature"],"available":False,
                        "error":str(exc)[:160]
                    })
        except (requests.RequestException, RuntimeError, ValueError,
                KeyError, TypeError) as exc:
            item["rpc_error"] = str(exc)[:160]
        cases.append(item)
    return {
        "mode":"PUMPSWAP_TRANSACTION_META_RESEARCH_ONLY",
        "cases":cases,
        "notes":[
            "Amounts are exact integer token-unit deltas, not dollar liquidity.",
            "Not all transaction token balances or relevant vaults are necessarily reported.",
            "Without independently matching PumpSwap base/quote vault account addresses these deltas cannot prove reserve withdrawal.",
            "Transaction metadata may be unavailable or RPC rate limited.",
            "Even a confirmed vault delta in one transaction is not a continuous pool reserve history."
        ],
        "database_writes":0,"trades_executed":0
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rpc",default="https://api.mainnet-beta.solana.com")
    p.add_argument("--each-side",type=int,default=2)
    args=p.parse_args()
    if not 1<=args.each_side<=3:
        p.error("--each-side must be 1..3")
    print(json.dumps(inspect(args.rpc,each_side=args.each_side),indent=2))


if __name__=="__main__":
    main()
