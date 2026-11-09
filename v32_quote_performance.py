"""Read-only V3.2 quote-path evaluation. NOT executed-trade P&L."""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def evaluate(position, tp=10.0, sl=-10.0, horizon=900.0, cost_pp=0.0):
    """First-observed TP/SL; otherwise time exit if observation covers horizon.
    Quote return is an estimate; cost_pp is a stress-test haircut, not measured fees.
    """
    if position.get("entry_token_raw") is None or not position.get("entry_quote_utc"):
        return {"outcome": "NO_ENTRY_QUOTE", "net_quote_return_pct": None}
    rows = []
    for q in position.get("exit_quotes", []):
        if q.get("status") != "QUOTE_AVAILABLE":
            continue
        try:
            age = float(q["age_seconds"])
            ret = float(q["estimated_return_pct"])
            if age >= 0 and -10000 < ret < 100000:
                rows.append((age, ret))
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
    if not rows:
        return {"outcome": "NO_EXIT_QUOTE", "net_quote_return_pct": None}
    rows.sort(key=lambda x: x[0])
    # Exit decisions must use information available at each observation, not future peaks.
    for age, ret in rows:
        if age > horizon:
            break
        if ret <= sl:
            return result("SL_OBSERVED", age, ret, cost_pp)
        if ret >= tp:
            return result("TP_OBSERVED", age, ret, cost_pp)
    # A timed exit is observable only if a quote lies at the horizon (within 1 sec),
    # or the first observation after horizon supplies a delayed approximation.
    at_time = [x for x in rows if abs(x[0] - horizon) <= 1]
    if at_time:
        return result("TIME_QUOTE", at_time[0][0], at_time[0][1], cost_pp)
    after = [x for x in rows if x[0] > horizon]
    if after:
        return result("TIME_DELAYED_QUOTE", after[0][0], after[0][1], cost_pp)
    return {"outcome": "CENSORED_BEFORE_HORIZON", "net_quote_return_pct": None,
            "last_quote_age_seconds": rows[-1][0], "last_quote_return_pct": rows[-1][1]}

def result(outcome, age, ret, cost_pp):
    return {"outcome": outcome, "exit_quote_age_seconds": round(age, 2),
            "gross_quote_return_pct": round(ret, 4),
            "assumed_extra_cost_pp": cost_pp,
            "net_quote_return_pct": round(ret - cost_pp, 4)}

def report(state, tp=10.0, sl=-10.0, horizon=900.0, cost_pp=0.0):
    positions = state.get("positions")
    if not isinstance(positions, dict):
        raise ValueError("V3.2 state must contain positions object")
    items = []
    for mint, p in sorted(positions.items()):
        if not isinstance(p, dict):
            continue
        e = evaluate(p, tp, sl, horizon, cost_pp)
        items.append({"mint": mint, "position_status": p.get("status"),
                      "entry_lag_seconds": p.get("entry_lag_seconds"),
                      **e})
    completed = [x["net_quote_return_pct"] for x in items if x.get("net_quote_return_pct") is not None]
    return {
        "mode": "READ_ONLY_QUOTE_SCENARIO_NOT_EXECUTED_PNL",
        "strategy": {"tp_pct": tp, "sl_pct": sl, "horizon_seconds": horizon,
                     "assumed_extra_cost_percentage_points": cost_pp},
        "summary": {"v32_positions": len(items), "observable_scenario_exits": len(completed),
                    "unresolved_or_unquoted": len(items) - len(completed),
                    "outcome_counts": dict(Counter(x["outcome"] for x in items)),
                    "average_net_quote_return_pct": (round(sum(completed)/len(completed), 4)
                                                     if completed else None),
                    "positive_scenario_exits": sum(v > 0 for v in completed),
                    "negative_scenario_exits": sum(v < 0 for v in completed)},
        "items": items,
        "limitations": [
            "Returns are estimated from quote snapshots, not filled trades.",
            "Entry is referenced to existing V3.2 quote baseline; no assumed historical execution.",
            "TP/SL may have occurred between snapshots and are only first-observed thresholds.",
            "Assumed extra costs are stress-test percentage points, not measured actual fees.",
            "Sample sizes count unique tokens, not 13 snapshots as independent trades.",
            "A time exit with its first quote after horizon is delayed and must not be presented as a precise 15-minute fill."
        ]
    }

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--v32", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--tp", type=float, default=10.0)
    p.add_argument("--sl", type=float, default=-10.0)
    p.add_argument("--horizon", type=float, default=900.0)
    p.add_argument("--extra-cost-pp", type=float, default=0.0)
    args = p.parse_args()
    if not (args.tp > 0 and args.sl < 0 and args.horizon > 0 and 0 <= args.extra_cost_pp <= 100):
        p.error("Require TP>0, SL<0, horizon>0 and 0<=extra-cost-pp<=100")
    data = json.loads(Path(args.v32).read_text(encoding="utf-8"))
    output = report(data, args.tp, args.sl, args.horizon, args.extra_cost_pp)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], indent=2, ensure_ascii=False))
    for item in output["items"]:
        print(item["mint"][:12], item["outcome"], item.get("net_quote_return_pct"))

if __name__ == "__main__":
    main()
