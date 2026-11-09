"""M1 V3.2 read-only, forward Jupiter quote result summary.

Evaluates the *stored* hypothetical token quantity without re-querying markets.
Positive quotes are not fills, and break-even excludes execution/network costs.
"""
import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

WINDOW_SECONDS=900
STAKE=Decimal("25")


def finite_amount(raw):
    try:
        if isinstance(raw, bool) or raw is None:
            return None
        value=Decimal(str(raw))
        return value if value.is_finite() and value >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def summarize_one(p):
    quotes=p.get("exit_quotes") or []
    valid=[]
    failed=0
    outside=0
    for row in quotes:
        try:
            elapsed=float(row["age_seconds"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not 0 <= elapsed < WINDOW_SECONDS:
            outside+=1
            continue
        amount=finite_amount(row.get("estimated_sell_usdc"))
        if row.get("status")=="QUOTE_AVAILABLE" and amount is not None:
            valid.append((elapsed, amount, row.get("checked_utc")))
        elif row.get("status")=="UNAVAILABLE":
            failed+=1
    valid.sort(key=lambda x:(x[0],x[2] or ""))
    high=max(valid,key=lambda x:x[1]) if valid else None
    low=min(valid,key=lambda x:x[1]) if valid else None
    breakeven=next((x for x in valid if x[1]>=STAKE),None)
    last=valid[-1] if valid else None

    def detail(x):
        if x is None:return None
        sec,amount,stamp=x
        return {"seconds_after_signal":round(sec,1),
                "checked_utc":stamp,
                "usdc":str(amount),
                "change_pct":str(round((amount/STAKE-1)*100,4))}

    return {"mint":p["mint"],
            "status":p.get("status"),
            "signal_utc":p.get("signal_utc"),
            "entry_quote_utc":p.get("entry_quote_utc"),
            "entry_lag_seconds":p.get("entry_lag_seconds"),
            "has_hypothetical_entry":p.get("entry_token_raw") is not None,
            "valid_exit_quotes":len(valid),
            "unavailable_exit_quotes":failed,
            "quotes_outside_15min_excluded":outside,
            "best_quote":detail(high),
            "worst_quote":detail(low),
            "first_quoted_breakeven":detail(breakeven),
            "last_valid_quote_within_window":detail(last),
            "observed_breakeven_before_execution_cost":breakeven is not None,
            "entry_error":p.get("error")}


def build_report(state):
    if state.get("version")!=1 or state.get("mode")!="M1_V3_2_FORWARD_QUOTES":
        raise ValueError("Unexpected M1 V3.2 state format")
    positions=state.get("positions")
    if not isinstance(positions,dict):
        raise ValueError("Invalid positions")
    items=[summarize_one(v) for v in positions.values()]
    available=[x for x in items if x["valid_exit_quotes"]>0]
    return {"mode":"M1_V3_2_READ_ONLY_QUOTE_REPORT",
            "summary":{"candidates":len(items),
                       "hypothetical_entries":sum(x["has_hypothetical_entry"] for x in items),
                       "candidates_with_exit_quote":len(available),
                       "exit_quotes_available":sum(x["valid_exit_quotes"] for x in items),
                       "exit_quotes_unavailable":sum(x["unavailable_exit_quotes"] for x in items),
                       "observed_breakeven_count":sum(x["observed_breakeven_before_execution_cost"] for x in items)},
            "positions":items,
            "limitations":[
                "25 USDC reference stake; current and earlier quote-only results, no executed orders.",
                "Breakeven means an available quote >=25 USDC, before network/priority/rent and execution costs.",
                "Sampling misses intra-interval maxima and crashes. Best observed quote is not a fill.",
                "A quote may become unavailable before a transaction would settle.",
                "Only observed quotes within 0 to <900 seconds from the original B3 signal are counted.",
            ]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    state=json.loads(Path(args.state).read_text(encoding="utf-8"))
    report=build_report(state)
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name(target.name+".tmp")
    tmp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    tmp.replace(target)
    print(json.dumps(report["summary"],ensure_ascii=False,indent=2))
    for row in report["positions"]:
        print(row["mint"][:12],row["status"],
              "best",row["best_quote"]["change_pct"] if row["best_quote"] else None,
              "worst",row["worst_quote"]["change_pct"] if row["worst_quote"] else None,
              "break_even_sec",row["first_quoted_breakeven"]["seconds_after_signal"] if row["first_quoted_breakeven"] else None)


if __name__=="__main__":
    main()
