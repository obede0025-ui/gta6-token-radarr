"""Read-only vault ID verification from the official PumpSwap Pool account layout.

Pool account layout: 8-byte Anchor discriminator, u8 bump, u16 index, then
creator/base_mint/quote_mint/lp_mint/base_vault/quote_vault (six pubkeys).
This reads CURRENT account state and matches vault addresses against already
captured historical transaction metadata. It does NOT independently retrieve
historical vault balances or prove a rug pull.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import requests
from research_public_solana_pool_probe import rpc
from research_pumpswap_vault_candidates import classify_report

PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DISCRIMINATOR = hashlib.sha256(b"account:Pool").digest()[:8]


def b58(data):
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, index = divmod(num, 58)
        out = ALPHABET[index] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def decode_pool(account, pool):
    if not isinstance(account, dict):
        return {"pool":pool,"status":"UNAVAILABLE"}
    if account.get("owner") != PROGRAM:
        return {"pool":pool,"status":"WRONG_OWNER"}
    data = account.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0],str):
        return {"pool":pool,"status":"INVALID_ACCOUNT_DATA"}
    try:
        raw = base64.b64decode(data[0], validate=True)
    except (ValueError, TypeError):
        return {"pool":pool,"status":"INVALID_ACCOUNT_DATA"}
    if len(raw) < 203 or raw[:8] != DISCRIMINATOR:
        return {"pool":pool,"status":"LAYOUT_OR_DISCRIMINATOR_MISMATCH"}
    fields = {
        "creator":b58(raw[11:43]),
        "base_mint":b58(raw[43:75]),
        "quote_mint":b58(raw[75:107]),
        "lp_mint":b58(raw[107:139]),
        "base_vault":b58(raw[139:171]),
        "quote_vault":b58(raw[171:203])
    }
    return {"pool":pool,"status":"POOL_LAYOUT_VERIFIED",
            "account_owner":account.get("owner"),**fields}


def analyze(meta_report, pools):
    candidates = classify_report(meta_report)["cases"]
    results = []
    for case, classified in zip(meta_report.get("cases", []),candidates):
        pool_info = pools.get(case.get("pool"),{"status":"UNAVAILABLE"})
        mint = case.get("mint")
        layout_ok = pool_info.get("status")=="POOL_LAYOUT_VERIFIED"
        valid = layout_ok and mint in (pool_info.get("base_mint"), pool_info.get("quote_mint"))
        expected = {}
        if valid:
            expected = {
                (pool_info["base_vault"],pool_info["base_mint"]):"BASE_VAULT",
                (pool_info["quote_vault"],pool_info["quote_mint"]):"QUOTE_VAULT"
            }
        matches = []
        for tx in case.get("transactions",[]):
            if tx.get("available") is not True or tx.get("transaction_error") is not None:
                continue
            for delta in tx.get("token_account_deltas",[]):
                key=(delta.get("token_account"),delta.get("mint"))
                if key not in expected:
                    continue
                matches.append({
                    "signature":tx.get("signature"),
                    "slot":tx.get("slot"),
                    "seconds_from_zero":tx.get("seconds_from_zero"),
                    "vault_type":expected[key],
                    "account":key[0],
                    "mint":key[1],
                    "pre_raw":delta.get("pre_raw"),
                    "post_raw":delta.get("post_raw"),
                    "delta_raw":delta.get("delta_raw")
                })
        results.append({
            "case":case.get("case"),"pool":case.get("pool"),
            "pool_layout_status":pool_info.get("status"),
            "pool_base_mint_matches_case":layout_ok and pool_info.get("base_mint")==mint,
            "pool_quote_mint_matches_case":layout_ok and pool_info.get("quote_mint")==mint,
            "decoded_base_mint":pool_info.get("base_mint") if layout_ok else None,
            "decoded_quote_mint":pool_info.get("quote_mint") if layout_ok else None,
            "base_vault":pool_info.get("base_vault") if layout_ok else None,
            "quote_vault":pool_info.get("quote_vault") if layout_ok else None,
            "mint_matches_either_pool_side":bool(valid),
            "verified_vault_addresses_in_successful_transactions":matches,
            "successful_transactions":classified["successful_transactions"],
            "failed_transactions":classified["failed_transactions"],
            "unavailable_transactions":classified["unavailable_transactions"],
            "historical_reserves_fully_verified":False,
            "classification":"UNVERIFIED"
        })
    return {"mode":"PUMPSWAP_OFFICIAL_POOL_LAYOUT_VAULT_MATCH",
            "cases":results,
            "limitations":[
                "Decoded addresses are from CURRENT pool account state, not historical account snapshots.",
                "A matching historical transaction establishes its pre/post vault balance at that transaction, not at the precise DexScreener zero timestamp.",
                "Swap-related changes are not proof that liquidity providers withdrew assets.",
                "Cannot reconstruct dollar liquidity or migration from these fragments alone."
            ],"database_writes":0,"trades_executed":0}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("json_report",help="Saved transaction meta report")
    p.add_argument("--rpc",default="https://api.mainnet-beta.solana.com")
    args=p.parse_args()
    report=json.loads(Path(args.json_report).read_text(encoding="utf-8"))
    addresses=list(dict.fromkeys(
        case.get("pool") for case in report.get("cases",[]) if case.get("pool")))
    with requests.Session() as session:
        accounts=rpc(session,args.rpc,"getMultipleAccounts",
                     [addresses,{"encoding":"base64","commitment":"finalized"}])
    values=(accounts or {}).get("value") or []
    pools={key:decode_pool(value,key)
           for key,value in zip(addresses,values)}
    print(json.dumps(analyze(report,pools),indent=2))


if __name__=="__main__":
    main()
