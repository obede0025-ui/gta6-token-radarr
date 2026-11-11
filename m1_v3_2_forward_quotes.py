"""M1 V3.2: forward-only, hypothetical Jupiter entry and repeated exit quotes.

Manual research pilot. Read M1 V3 state, write separate state, never place orders.
The entry is a quote captured AFTER discovery, never reconstructed historically.
"""
import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from radar_quote_monitor import (
    quote, sanitize_error, USDC, USDC_DECIMALS, token_amount_to_raw,
)

WINDOW_SECONDS = 900
MAX_ENTRY_LAG_SECONDS = 360
MIN_EXIT_INTERVAL_SECONDS = 60
MAX_ACTIVE = 2
MAX_REQUESTS = 4
STAKE = Decimal("25")
MAX_RECORDS = 500
MAX_ROUNDTRIP_LOSS_PCT = Decimal("3")  # Same research threshold as B2.


def timestamp(s):
    stamp = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timezone required")
    return stamp


def candidate_key(item):
    return (item["signal"], item["mint"])


def bootstrap(m1_state, now):
    entries = m1_state.get("tracked")
    if m1_state.get("version") != 1 or not isinstance(entries, dict):
        raise ValueError("Invalid M1 state")
    ordered = sorted(entries.values(), key=candidate_key)
    return {"version":1,"mode":"M1_V3_2_FORWARD_QUOTES",
            "created_utc":now.isoformat(),
            "cursor":list(candidate_key(ordered[-1])) if ordered else None,
            "positions":{}}


def evaluate(state, m1_state, now, quote_fn, preentry_gate=False):
    if state.get("version") != 1 or state.get("mode") != "M1_V3_2_FORWARD_QUOTES":
        raise ValueError("Invalid V3.2 state")
    if not isinstance(state.get("positions"), dict):
        raise ValueError("Invalid positions")
    entries = m1_state.get("tracked")
    if m1_state.get("version") != 1 or not isinstance(entries, dict):
        raise ValueError("Invalid M1 source")
    requests_used = 0
    def attempt(input_mint, output_mint, amount):
        nonlocal requests_used
        if requests_used >= MAX_REQUESTS:
            raise RuntimeError("REQUEST_LIMIT")
        requests_used += 1
        return quote_fn(input_mint, output_mint, amount)

    for item in sorted(entries.values(), key=candidate_key):
        key = list(candidate_key(item))
        if state["cursor"] is not None and key <= state["cursor"]:
            continue
        state["cursor"] = key
        if item.get("status") not in ("WATCHING", "OBSERVED_PRICE_TRIGGER"):
            continue
        mint = item["mint"]
        if mint in state["positions"]:
            continue
        age = (now - timestamp(item["signal"])).total_seconds()
        live = sum(p["status"] == "ACTIVE" for p in state["positions"].values())
        if age < 0 or age > MAX_ENTRY_LAG_SECONDS:
            status = "SKIPPED_STALE"
        elif live >= MAX_ACTIVE:
            status = "SKIPPED_CAPACITY"
        else:
            status = "NEEDS_ENTRY"
        state["positions"][mint] = {
            "mint":mint, "signal_utc":item["signal"], "status":status,
            "first_seen_utc":now.isoformat(),
            "m1_registered_utc":item.get("registered_utc"),
            "b3_recorded_utc":item.get("b3_recorded_utc"),
            "entry_quote_utc":None, "entry_token_raw":None,
            "entry_lag_seconds":round(age,1),
            "exit_quotes":[], "error":None,
        }
    for p in sorted(state["positions"].values(),key=lambda x:x["signal_utc"]):
        if p["status"] in ("SKIPPED_STALE", "SKIPPED_CAPACITY", "ENTRY_UNAVAILABLE",
                           "ENTRY_BLOCKED_ROUNDTRIP", "ENTRY_BLOCKED_NO_ROUTE",
                           "WINDOW_EXPIRED"):
            continue
        elapsed = (now - timestamp(p["signal_utc"])).total_seconds()
        if elapsed >= WINDOW_SECONDS:
            p["status"] = "WINDOW_EXPIRED"
            continue
        mint=p["mint"]
        if p["status"] == "NEEDS_ENTRY":
            if elapsed > MAX_ENTRY_LAG_SECONDS:
                p["status"] = "SKIPPED_STALE"
                continue
            live = sum(v["status"] == "ACTIVE" for v in state["positions"].values())
            if live >= MAX_ACTIVE:
                p["status"] = "SKIPPED_CAPACITY"
                continue
            # A gated entry requires TWO remaining quote slots (buy and immediate sell).
            # Existing legacy service behavior is unchanged with flag disabled.
            if requests_used + (2 if preentry_gate else 1) > MAX_REQUESTS:
                continue
            try:
                buy=attempt(USDC,mint,token_amount_to_raw(STAKE,USDC_DECIMALS))
                if preentry_gate:
                    raw=buy.get("output_raw")
                    if (buy.get("quote_only") is not True or buy.get("error_code")
                            or isinstance(raw, bool) or not isinstance(raw, int)
                            or raw <= 0):
                        raise ValueError("INVALID_ENTRY_ROUTE")
                    sell=attempt(mint,USDC,raw)
                    sell_raw=sell.get("output_raw")
                    if (sell.get("quote_only") is not True or sell.get("error_code")
                            or isinstance(sell_raw, bool) or not isinstance(sell_raw, int)
                            or sell_raw <= 0):
                        raise ValueError("INVALID_SELL_ROUTE")
                    amount=Decimal(sell_raw)/Decimal(10**USDC_DECIMALS)
                    pct=(amount/STAKE-1)*100
                    p["preentry_roundtrip_pct"]=str(round(pct,4))
                    p["preentry_gate"]="BLOCK" if pct < -MAX_ROUNDTRIP_LOSS_PCT else "PASS"
                    if p["preentry_gate"] == "BLOCK":
                        p["status"]="ENTRY_BLOCKED_ROUNDTRIP"
                        continue
                    # The immediate sell-back quote is an observation, NOT a real fill.
                    p["exit_quotes"].append({
                        "checked_utc":now.isoformat(), "age_seconds":round(elapsed,1),
                        "estimated_sell_usdc":str(amount),
                        "estimated_return_pct":str(round(pct,4)),
                        "status":"QUOTE_AVAILABLE",
                    })
                p["entry_token_raw"]=str(buy["output_raw"])
                p["entry_quote_utc"]=now.isoformat()
                p["status"]="ACTIVE"
            except Exception as exc:
                p["status"]="ENTRY_BLOCKED_NO_ROUTE" if preentry_gate else "ENTRY_UNAVAILABLE"
                p["preentry_gate"]="BLOCK" if preentry_gate else None
                p["error"]=sanitize_error(exc)
                continue
            # Reuse the immediate roundtrip quote instead of requesting a third quote.
            if preentry_gate:
                continue
        if p["status"] != "ACTIVE" or requests_used>=MAX_REQUESTS:
            continue
        if p["exit_quotes"]:
            last=timestamp(p["exit_quotes"][-1]["checked_utc"])
            if (now-last).total_seconds() < MIN_EXIT_INTERVAL_SECONDS:
                continue
        try:
            sell=attempt(mint,USDC,int(p["entry_token_raw"]))
            amount=Decimal(sell["output_raw"])/Decimal(10**USDC_DECIMALS)
            pct=(amount/STAKE-1)*100
            p["exit_quotes"].append({
                "checked_utc":now.isoformat(),
                "age_seconds":round(elapsed,1),
                "estimated_sell_usdc":str(amount),
                "estimated_return_pct":str(round(pct,4)),
                "status":"QUOTE_AVAILABLE",
            })
        except Exception as exc:
            p["exit_quotes"].append({
                "checked_utc":now.isoformat(),
                "age_seconds":round(elapsed,1),
                "status":"UNAVAILABLE",
                "error":sanitize_error(exc),
            })
        p["exit_quotes"]=p["exit_quotes"][-20:]
    # Bound retained records while preserving currently active entries.
    if len(state["positions"])>MAX_RECORDS:
        old=sorted((v for v in state["positions"].values() if v["status"]!="ACTIVE"),
                   key=lambda v:v["signal_utc"])
        for item in old[:len(state["positions"])-MAX_RECORDS]:
            del state["positions"][item["mint"]]
    return {"jupiter_requests":requests_used,
            "preentry_gate_enabled":bool(preentry_gate),
            "preentry_blocked":sum(p["status"] in ("ENTRY_BLOCKED_ROUNDTRIP", "ENTRY_BLOCKED_NO_ROUTE") for p in state["positions"].values()),
            "active":sum(p["status"]=="ACTIVE" for p in state["positions"].values()),
            "tracked_total":len(state["positions"]),
            "entries_with_quote":sum(p["entry_token_raw"] is not None
                                     for p in state["positions"].values())}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--m1-state",required=True)
    parser.add_argument("--state",required=True)
    parser.add_argument("--preentry-gate", action="store_true",
        help="Experimental B2-style 3%% roundtrip gate; use isolated state and timer only.")
    args=parser.parse_args()
    target=Path(args.state)
    target.parent.mkdir(parents=True,exist_ok=True)
    # Never overlap writes even if accidentally executed twice.
    with target.with_name(target.name+".lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        m1=json.loads(Path(args.m1_state).read_text(encoding="utf-8"))
        now=datetime.now(timezone.utc)
        if not target.exists():
            state=bootstrap(m1,now)
            with target.open("x",encoding="utf-8") as f:
                json.dump(state,f,indent=2)
            print(json.dumps({"bootstrapped":True,"note":"Old signals excluded; no Jupiter requests"}))
            return
        state=json.loads(target.read_text(encoding="utf-8"))
        api_key=os.environ.get("JUPITER_API_KEY","").strip()
        if not api_key:
            raise SystemExit("NOT_CONFIGURED: No key, no Jupiter requests.")
        with requests.Session() as session:
            result=evaluate(state,m1,now,
                lambda input_mint,output_mint,raw:quote(
                    session,api_key,input_mint,output_mint,raw),
                preentry_gate=args.preentry_gate)
        temp=target.with_name(target.name+".tmp")
        temp.write_text(json.dumps(state,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        temp.replace(target)
        print(json.dumps(result))


if __name__=="__main__":
    main()
