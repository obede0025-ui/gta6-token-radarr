"""Read-only bounded PumpSwap vault-balance timeline near DexScreener zeros.

Fetches a capped number of finalized signatures around each zero timestamp,
matches only official PumpSwap Pool vaults, and reports exact transaction-level
pre/post reserves. Does not infer within-slot order or historical continuous
liquidity. No on-chain writes, no DB writes and no executed trades.
"""
import argparse
import json
from datetime import datetime, timezone
import requests
from research_public_solana_pool_probe import CASES, rpc
from research_pumpswap_official_vault_match import decode_pool
from research_pumpswap_transaction_meta import tx_summary, select_signatures

WSOL = "So11111111111111111111111111111111111111112"


def summarize_case(case, pool_data, signature_rows, transactions, each_side=5, rpc_error_signatures=None):
    """transactions is dict signature -> parsed RPC tx or None."""
    info = decode_pool(pool_data, case["pair"])
    match = (info.get("status") == "POOL_LAYOUT_VERIFIED"
             and case["mint"] in (info.get("base_mint"), info.get("quote_mint")))
    outcome = {
        "case":case["label"], "pool":case["pair"], "zero_utc":case["zero_utc"],
        "pool_layout_status":info.get("status"), "mint_matches_pool":bool(match),
        "state":"UNVERIFIED", "historical_reserves_fully_verified":False,
        "vault_balance_events":[], "failed_transactions":0,
        "missing_transactions":0,
    }
    if not match:
        outcome["blocker"] = "Account layout or mint validation failed"
        return outcome
    expected = {
        (info["base_vault"],info["base_mint"]):"BASE_VAULT",
        (info["quote_vault"],info["quote_mint"]):"QUOTE_VAULT",
    }
    target=int(datetime.fromisoformat(case["zero_utc"]).timestamp())
    selected=select_signatures(signature_rows,target,each_side)
    outcome["selected_signatures"]=len(selected)
    timestamps=[s.get("blockTime") for s in signature_rows
                if isinstance(s.get("blockTime"),int)]
    outcome["sampled_history_spans_zero"]=bool(
        timestamps and min(timestamps)<=target<=max(timestamps))
    events=[]
    rpc_error_signatures = set(rpc_error_signatures or [])
    outcome["rpc_error_transactions"] = 0
    outcome["not_requested_transactions"] = 0
    for sig in selected:
        transaction=transactions.get(sig["signature"])
        if transaction is None:
            if sig["signature"] in rpc_error_signatures:
                outcome["rpc_error_transactions"]+=1
            else:
                outcome["not_requested_transactions"]+=1
            outcome["missing_transactions"]+=1
            continue
        parsed=tx_summary(sig["signature"],transaction)
        if parsed["transaction_error"] is not None:
            outcome["failed_transactions"]+=1
            continue
        reserves={}
        for delta in parsed["token_account_deltas"]:
            key=delta["token_account"],delta["mint"]
            if key in expected:
                reserves[expected[key]]={
                    "account":key[0],"mint":key[1],
                    "pre_raw":delta["pre_raw"],
                    "post_raw":delta["post_raw"],
                    "delta_raw":delta["delta_raw"],
                    "decimals":delta["decimals"]
                }
        events.append({
            "signature":sig["signature"],"slot":parsed["slot"],
            "block_time":parsed["block_time"],
            "seconds_from_zero":sig["blockTime"]-target,
            "vault_balances":reserves,
            "both_vaults_changed":len(reserves)==2,
        })
    # Slot-level ordering is reliable. Same-slot order is NOT inferred from signatures.
    events.sort(key=lambda e:(e["slot"] if e["slot"] is not None else -1))
    per_slot={}
    for ev in events:
        per_slot[ev["slot"]]=per_slot.get(ev["slot"],0)+1
    for ev in events:
        ev["within_slot_order_unknown"]=per_slot[ev["slot"]]>1
    outcome["vault_balance_events"]=events
    outcome["transactions_with_vault_changes"]=sum(bool(e["vault_balances"]) for e in events)
    outcome["transactions_with_both_vaults_changed"]=sum(e["both_vaults_changed"] for e in events)
    outcome["events_before_zero"]=sum(e["seconds_from_zero"]<=0 for e in events)
    outcome["events_after_zero"]=sum(e["seconds_from_zero"]>0 for e in events)
    outcome["timeline_complete"]=False
    return outcome


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--rpc",default="https://api.mainnet-beta.solana.com")
    ap.add_argument("--each-side",type=int,default=5)
    args=ap.parse_args()
    if not 1<=args.each_side<=10:
        ap.error("--each-side must be between 1 and 10")
    results=[]
    halted_by_rate_limit=False
    with requests.Session() as session:
        for case in CASES:
            if halted_by_rate_limit:
                results.append({"case":case["label"], "pool":case["pair"], "state":"UNVERIFIED", "not_requested_due_to_rpc_rate_limit":True, "historical_reserves_fully_verified":False})
                continue
            pool_data=None
            sig_rows=[]
            transactions={}
            errors=[]
            rpc_error_signatures=[]
            try:
                response=rpc(session,args.rpc,"getAccountInfo",
                    [case["pair"],{"encoding":"base64","commitment":"finalized"}])
                pool_data=(response or {}).get("value")
                sig_rows=rpc(session,args.rpc,"getSignaturesForAddress",
                    [case["pair"],{"limit":1000}]) or []
                selected=select_signatures(
                    sig_rows,int(datetime.fromisoformat(case["zero_utc"]).timestamp()),
                    args.each_side)
                for sig in selected:
                    try:
                        transactions[sig["signature"]]=rpc(session,args.rpc,"getTransaction",[
                            sig["signature"],{"encoding":"jsonParsed",
                                "commitment":"finalized",
                                "maxSupportedTransactionVersion":0}])
                    except (requests.RequestException,RuntimeError,ValueError,TypeError) as exc:
                        errors.append(str(exc)[:150])
                        rpc_error_signatures.append(sig["signature"])
                        if "429" in str(exc) or "rate limit" in str(exc).lower():
                            halted_by_rate_limit=True
                            break
            except (requests.RequestException,RuntimeError,ValueError,TypeError) as exc:
                errors.append(str(exc)[:150])
                if "429" in str(exc) or "rate limit" in str(exc).lower():
                    halted_by_rate_limit=True
            item=summarize_case(case,pool_data,sig_rows,transactions,args.each_side,rpc_error_signatures)
            item["rpc_errors"]=errors
            item["rpc_rate_limited"]=halted_by_rate_limit
            results.append(item)
    print(json.dumps({
        "mode":"READ_ONLY_BOUNDED_PUMPSWAP_VAULT_TIMELINE",
        "cases":results,
        "stopped_due_to_rpc_rate_limit":halted_by_rate_limit,
        "limitations":[
            "Only a capped signature sample; missing events may change reserve interpretation.",
            "No deterministic within-slot transaction order without block transaction indices.",
            "Only vaults with pre AND post token balance metadata are reported.",
            "This is not a proof of LP withdrawal, actual executable market liquidity, or scanner latency.",
            "RPC 429 and missing metadata never imply zero on-chain reserves."
        ],"database_writes":0,"trades_executed":0
    },indent=2))


if __name__=="__main__":
    main()
