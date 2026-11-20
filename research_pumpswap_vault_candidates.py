"""Classify PumpSwap transaction-meta evidence conservatively, offline."""
def classify_case(case):
    pool = case.get("pool")
    summary = {
        "case":case.get("case"),
        "successful_transactions":0,
        "failed_transactions":0,
        "unavailable_transactions":0,
        "pool_owned_token_account_candidates":[],
        "historical_pool_reserves_verified":False,
        "classification":"UNVERIFIED"
    }
    found={}
    for tx in case.get("transactions",[]):
        if not tx.get("available"):
            summary["unavailable_transactions"] += 1
            continue
        if tx.get("transaction_error") is not None:
            summary["failed_transactions"] += 1
            continue
        summary["successful_transactions"] += 1
        for delta in tx.get("token_account_deltas",[]):
            if delta.get("owner_reported") != pool:
                continue
            account=delta.get("token_account")
            mint=delta.get("mint")
            if not account or not mint:
                continue
            entry=found.setdefault((account,mint),{
                "token_account":account,
                "mint":mint,
                "sampled_successful_deltas":0,
                "verified_as_pumpswap_vault":False
            })
            entry["sampled_successful_deltas"]+=1
    summary["pool_owned_token_account_candidates"]=list(found.values())
    return summary


def classify_report(report):
    return {
        "mode":"OFFLINE_PUMPSWAP_VAULT_CANDIDATE_AUDIT",
        "cases":[classify_case(c) for c in report.get("cases",[])],
        "historical_pool_reserves_verified":False,
        "limitations":[
            "Owner match is only a vault candidate, not proof of PumpSwap vault account layout.",
            "A failed transaction is not an executed reserve movement.",
            "429 responses are missing evidence, not an on-chain liquidity event.",
            "Signed transactions are not executed by this offline classifier."
        ],
        "database_writes":0,"trades_executed":0
    }


def main():
    import argparse, json
    parser=argparse.ArgumentParser()
    parser.add_argument("json_report",help="Saved JSON output from research_pumpswap_transaction_meta.py")
    args=parser.parse_args()
    with open(args.json_report,encoding="utf-8") as f:
        report=json.load(f)
    print(json.dumps(classify_report(report),indent=2))


if __name__=="__main__":
    main()
