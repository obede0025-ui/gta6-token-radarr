"""Read-only public Solana RPC probe for three historical liquidity-zero cases.

Identifies the current account owner and whether transaction history around the
historical observation can be retrieved. Does NOT reconstruct historic reserves.
No secrets, no orders, no writes or state changes; RPC calls are public reads.
"""
import argparse
import json
from datetime import datetime, timezone
import requests

CASES = [
    {"label":"STILL_ZERO", "mint":"AbBZCfZDGmPwVfJBLUXUCBSr2G44qoF81PjiRQgF2a9M",
     "pair":"64jSpDTALJQsrbXuWeyLzTJS4oFwDryLHCwiZcEpGx6R",
     "before_utc":"2026-11-17T14:21:29+00:00",
     "zero_utc":"2026-11-17T14:22:31+00:00"},
    {"label":"LOW_LP_RECOVERY","mint":"DVyx9czvHJ2aCub1XSbvNdyRBhP95MG6bWk6W3dtXn2j",
     "pair":"7wADSFVkYrZ1HKvENCSJayrQRVJDG6e6ktbPsveweB39",
     "before_utc":"2026-11-19T07:38:28+00:00",
     "zero_utc":"2026-11-19T07:39:30+00:00"},
    {"label":"NO_FIVE_MIN_OBSERVATION","mint":"4ytb7hgFGKWzNdBTRUzi82AjXQ6nxo4hA5T2vrcSHk9u",
     "pair":"EakLE5tpCL27gAuhx6vJEGZ57js4K4hUbmY4K24NJAFn",
     "before_utc":"2026-11-19T02:27:43+00:00",
     "zero_utc":"2026-11-19T02:28:45+00:00"},
]


def rpc(session, endpoint, method, params):
    response = session.post(endpoint,
        json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
        timeout=25)
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(str(result["error"])[:300])
    return result.get("result")


def evaluate_case(case, account, signatures):
    """No transaction list or current account can prove historical reserves."""
    target = int(datetime.fromisoformat(case["zero_utc"]).timestamp())
    timed = [
        s for s in signatures
        if isinstance(s.get("blockTime"), (int,float))
    ]
    nearby = [
        s for s in timed
        if abs(s["blockTime"] - target) <= 900
    ]
    earliest = min((s["blockTime"] for s in timed), default=None)
    latest = max((s["blockTime"] for s in timed), default=None)
    return {
        "case": case["label"],
        "mint": case["mint"],
        "pair": case["pair"],
        "historical_zero_utc": case["zero_utc"],
        "current_pair_account_exists": account is not None,
        "current_owner_program": account.get("owner") if account else None,
        "current_executable": account.get("executable") if account else None,
        "sampled_signatures": len(signatures),
        "signatures_within_15m": len(nearby),
        "nearest_signature": min(
            ({"signature":s.get("signature"),"slot":s.get("slot"),
              "blockTime":s.get("blockTime"),
              "seconds_from_zero":s["blockTime"]-target} for s in nearby),
            key=lambda x: abs(x["seconds_from_zero"]), default=None
        ),
        "signature_sample_covers_target": bool(
            earliest is not None and earliest <= target <= latest
        ),
        "historical_reserves_verified": False,
        "result": "UNVERIFIED",
        "reason": "Current account and signature history do not prove historical token vault balances."
    }


def probe(endpoint, session=None, limit=1000):
    session = session or requests.Session()
    accounts = rpc(session, endpoint, "getMultipleAccounts", [
        [c["pair"] for c in CASES], {"encoding":"base64", "commitment":"finalized"}
    ])["value"]
    results = []
    for case, account in zip(CASES, accounts):
        try:
            signatures = rpc(session, endpoint, "getSignaturesForAddress", [
                case["pair"], {"limit":limit}
            ]) or []
            results.append(evaluate_case(case, account, signatures))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            result = evaluate_case(case, account, [])
            result["rpc_error"] = str(exc)[:200]
            results.append(result)
    return {
        "mode":"SOLANA_PUBLIC_RPC_READ_ONLY_PROBE",
        "rpc_endpoint":endpoint,
        "cases":results,
        "limitations":[
            "Current getMultipleAccounts does not reconstruct historical pool reserves.",
            "A limited transaction signature sample can miss the event time.",
            "Pool address owner alone cannot identify reserve vaults for all AMM types.",
            "Archive RPC and DEX-specific pool layout are needed to confirm historic vault balances.",
            "All cases remain UNVERIFIED until independent historical reserve evidence exists."
        ],
        "database_writes":0, "trades_executed":0
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default="https://api.mainnet-beta.solana.com",
        help="Public read-only Solana JSON-RPC URL (never paste paid API credentials in chat)")
    parser.add_argument("--signature-limit", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.signature_limit <= 1000:
        parser.error("signature-limit must be 1..1000")
    try:
        output = probe(args.rpc, limit=args.signature_limit)
    except (requests.RequestException, ValueError, RuntimeError, TypeError, KeyError) as exc:
        output = {"mode":"SOLANA_PUBLIC_RPC_READ_ONLY_PROBE",
                  "error":str(exc)[:300],
                  "historical_reserves_verified":False,
                  "result":"UNVERIFIED",
                  "database_writes":0,"trades_executed":0}
    print(json.dumps(output,indent=2))


if __name__ == "__main__":
    main()
