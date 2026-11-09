"""M1 V3.1 manual, bounded Jupiter quote-only roundtrip audit.

Reads an existing M1 forward-monitor state; no wallet, signing, transactions,
portfolio mutation, or scheduled service. Quotes are *current* indicative
roundtrips, not a historical exit fill.
"""
import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import requests
from radar_quote_monitor import quote, sanitize_error, USDC, USDC_DECIMALS, token_amount_to_raw

STAKE = Decimal("25")
MAX_TOKENS = 2
COOLDOWN_SECONDS = 900


def candidates(state):
    if state.get("version") != 1 or not isinstance(state.get("tracked"), dict):
        raise ValueError("Invalid M1 V3 state")
    rows = [v for v in state["tracked"].values()
            if v.get("status") in ("WATCHING", "OBSERVED_PRICE_TRIGGER")]
    # Candidate priority is newest actual signal. No mint string is trusted
    # until quote() validates its base58 shape.
    rows.sort(key=lambda x: x.get("signal", ""), reverse=True)
    return rows


def audit_one(item, session, api_key, stake=STAKE):
    mint = item["mint"]
    entry = {"mint": mint, "signal_utc": item.get("signal"),
             "m1_status": item.get("status"), "status": "UNAVAILABLE",
             "quote_only": True, "stake_usdc": str(stake),
             "returned_usdc": None, "roundtrip_change_pct": None,
             "buy_router": None, "sell_router": None, "warning": None}
    try:
        stake_raw = token_amount_to_raw(stake, USDC_DECIMALS)
        buy = quote(session, api_key, USDC, mint, stake_raw)
        # This sell is for the number of tokens quoted in the hypothetical
        # purchase NOW. It is NOT a position owned since the signal.
        sell = quote(session, api_key, mint, USDC, buy["output_raw"])
        returned = Decimal(sell["output_raw"]) / Decimal(10 ** USDC_DECIMALS)
        entry.update(status="QUOTE_AVAILABLE",
                     returned_usdc=str(returned),
                     roundtrip_change_pct=str(round((returned/stake-1)*100, 4)),
                     buy_router=buy.get("router"),
                     sell_router=sell.get("router"))
    except Exception as exc:
        entry["warning"] = sanitize_error(exc)
    return entry


def run(state, previous, *, now, session, api_key):
    if not api_key:
        raise ValueError("Missing JUPITER_API_KEY; no requests attempted")
    result = {"version":1, "mode":"M1_V3_1_QUOTE_ONLY",
              "checked_utc":now.isoformat(),
              "notes":["Current USDC->token->USDC quote, not historical entry/exit.",
                       "Not a fill; fees for network, priority, rent and execution are excluded.",
                       "No taker, wallet, transaction building, signing, or orders."],
              "tokens":[], "skipped_recent":0}
    prior = {}
    if previous:
        if previous.get("mode") != "M1_V3_1_QUOTE_ONLY":
            raise ValueError("Unexpected audit cache format")
        for record in previous.get("tokens", []):
            if isinstance(record, dict) and isinstance(record.get("mint"), str):
                prior[record["mint"]] = previous.get("checked_utc")
    for item in candidates(state):
        if len(result["tokens"]) >= MAX_TOKENS:
            break
        mint = item.get("mint")
        if not isinstance(mint,str):
            continue
        old_time = prior.get(mint)
        if old_time:
            try:
                stamp=datetime.fromisoformat(old_time.replace("Z","+00:00"))
                if 0 <= (now-stamp).total_seconds() < COOLDOWN_SECONDS:
                    result["skipped_recent"] += 1
                    continue
            except ValueError:
                pass
        result["tokens"].append(audit_one(item,session,api_key))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--state",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    key=os.environ.get("JUPITER_API_KEY","").strip()
    if not key:
        raise SystemExit("NOT_CONFIGURED: JUPITER_API_KEY missing. No requests sent.")
    state=json.loads(Path(args.state).read_text(encoding="utf-8"))
    target=Path(args.output)
    previous=json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    now=datetime.now(timezone.utc)
    with requests.Session() as session:
        result=run(state,previous,now=now,session=session,api_key=key)
    if not result["tokens"] and result["skipped_recent"]:
        print(json.dumps({"status":"COOLDOWN","skipped_recent":result["skipped_recent"]}))
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".tmp")
    temp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    temp.replace(target)
    print(json.dumps({"status":"OK","quotes_attempted":len(result["tokens"]),
                      "quotes_available":sum(x["status"]=="QUOTE_AVAILABLE" for x in result["tokens"]),
                      "skipped_recent":result["skipped_recent"],
                      "output":str(target)}))
if __name__=="__main__":
    main()
