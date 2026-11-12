"""Read-only chronology-aware V3.2 research: test pre-entry filters, no trades.

Exploratory results, not validated trading profitability. Features are drawn only
from the latest SQLite snapshot at/before the hypothetical entry and at/before
the M1 signal. Outcome is the first observed Jupiter quote exit, not a fill.
No state, timers, credentials or existing database are modified.
"""
import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from v32_quote_performance import evaluate

EXIT_TYPES = {"TP_OBSERVED", "SL_OBSERVED", "TIME_QUOTE"}
FEATURES = {
    "liquidity_usd": [20000, 25000, 30000, 40000, 50000],
    "volume_m5": [250, 500, 1000, 2000, 3000, 5000],
    "buy_share": [0.50, 0.55, 0.60, 0.65],
    "entry_lag_seconds": [120, 180, 240, 300, 360],
    "pair_age_hours": [0.25, 0.5, 1, 2, 4],
}


def parse(iso):
    if not isinstance(iso, str):
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except ValueError:
        return None


def finite(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def load_positions(state, conn):
    out, excluded = [], Counter()
    for mint, pos in state.get("positions", {}).items():
        outcome = evaluate(pos, tp=10, sl=-10, horizon=900, cost_pp=2)
        result = outcome.get("net_quote_return_pct")
        signal, entry = parse(pos.get("signal_utc")), parse(pos.get("entry_quote_utc"))
        if result is None or outcome.get("outcome") not in EXIT_TYPES:
            excluded[outcome.get("outcome", "UNKNOWN")] += 1
            continue
        if signal is None or entry is None or entry < signal:
            excluded["INVALID_TIMESTAMPS"] += 1
            continue
        # Do not use snapshots after the signal, even if they preceded the quote.
        # Their data could have been unknown when the research candidate emerged.
        row = conn.execute("""
            SELECT scanned_at, liquidity_usd, volume_m5, buys_m5, sells_m5,
                   pair_age_hours, pair_address
            FROM snapshots
            WHERE token_address=? AND datetime(scanned_at)<=datetime(?)
            ORDER BY datetime(scanned_at) DESC LIMIT 1
        """, (mint, signal.isoformat())).fetchone()
        if not row:
            excluded["NO_PRE_SIGNAL_SNAPSHOT"] += 1
            continue
        snap_time = parse(row[0])
        if snap_time is None or snap_time > signal:
            excluded["SNAPSHOT_TIMESTAMP_INVALID"] += 1
            continue
        liq, vol, buys, sells, age = (finite(v) for v in row[1:6])
        if any(v is None for v in (liq, vol, buys, sells, age)):
            excluded["MISSING_FEATURES"] += 1
            continue
        if buys + sells <= 0:
            excluded["NO_BUY_SELL_SAMPLE"] += 1
            continue
        out.append({
            "mint": mint, "signal": signal.isoformat(), "pair": row[6],
            "snapshot_age_seconds": (signal - snap_time).total_seconds(),
            "entry_lag_seconds": (entry-signal).total_seconds(),
            "liquidity_usd": liq, "volume_m5": vol,
            "buy_share": buys/(buys+sells), "pair_age_hours": age,
            "quote_return_pct": float(result), "outcome": outcome["outcome"],
            # Retain original raw modeled return, but spot cannot lose >100%.
            "bounded_quote_return_pct": max(-100.0, float(result)),
        })
    out.sort(key=lambda x: (x["signal"], x["mint"]))
    return out, excluded


def metrics(rows):
    values = [r["bounded_quote_return_pct"] for r in rows]
    if not values:
        return {"n": 0, "mean_pct": None}
    return {
        "n": len(values),
        "mean_pct": round(statistics.mean(values), 4),
        "median_pct": round(statistics.median(values), 4),
        "win_rate_pct": round(100 * sum(v > 0 for v in values)/len(values), 2),
        "max_loss_pct": round(min(values), 4),
        "extreme_losses_below_minus_40_pct": sum(v < -40 for v in values),
        "total_quote_return_points": round(sum(values), 4),
    }


def passes(row, rule):
    name, direction, threshold = rule
    value = row.get(name)
    return value is not None and ((value >= threshold) if direction == ">=" else (value <= threshold))


def generate_rules():
    rules = []
    for key, values in FEATURES.items():
        for threshold in values:
            direction = "<=" if key in ("entry_lag_seconds", "pair_age_hours") else ">="
            rules.append((key, direction, threshold))
    return rules


def evaluate_rules(train, test, min_train, min_test):
    candidates = []
    for rule in generate_rules():
        kept_train = [r for r in train if passes(r, rule)]
        kept_test = [r for r in test if passes(r, rule)]
        if len(kept_train) < min_train:
            continue
        # Selection solely on the training segment; no test metric in ranking.
        tr = metrics(kept_train)
        score = tr["mean_pct"] - 0.5 * (
            sum(abs(min(0, r["bounded_quote_return_pct"])) for r in kept_train)
            / len(kept_train)
        )
        candidates.append({
            "rule": rule, "score_train": round(score, 4),
            "train": tr, "test": metrics(kept_test),
            "test_sufficient": len(kept_test) >= min_test,
            "train_coverage_pct": round(100*len(kept_train)/len(train), 2),
            "test_coverage_pct": round(100*len(kept_test)/len(test), 2) if test else None,
        })
    candidates.sort(key=lambda x: (-x["score_train"], -x["train"]["n"], str(x["rule"])))
    return candidates


def report(state, db, train_share=0.6):
    rows, exclusions = load_positions(state, db)
    if len(rows) < 15:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "usable_positions": len(rows),
            "exclusions": dict(exclusions),
            "all_metrics": metrics(rows),
            "recommendation": "Do not optimize or deploy rules on this dataset.",
        }
    cut = max(1, min(len(rows)-1, int(len(rows)*train_share)))
    train, test = rows[:cut], rows[cut:]
    min_train = max(8, int(len(train)*0.3))
    min_test = max(4, int(len(test)*0.25))
    rules = evaluate_rules(train, test, min_train, min_test)
    top = rules[:10]
    return {
        "mode": "EXPLORATORY_CHRONOLOGICAL_QUOTE_RESEARCH_NO_TRADES",
        "status": "RESEARCH_ONLY",
        "assumptions": {"tp_pct": 10, "sl_pct": -10, "horizon_s": 900,
                        "extra_cost_stress_pct_points": 2,
                        "train_fraction": train_share,
                        "selection": "single filters, train only; untouched chronological test"},
        "counts": {"positions_total": len(state.get("positions", {})),
                   "usable_exits_with_pre_signal_features": len(rows),
                   "training": len(train), "testing": len(test),
                   "excluded": dict(exclusions)},
        "baseline": {"all": metrics(rows), "train": metrics(train),
                     "test": metrics(test)},
        "best_training_rule": top[0] if top else None,
        "candidate_rules_top10": top,
        "all_rules_training_ranked": rules,
        "per_position": rows,
        "limitations": [
            "Small, overlapping, sequentially selected tokens; historical paper quotes not executed returns.",
            "TP and SL are observed only when quotes were polled, not intrabar fills.",
            "Censored, missing-entry, and delayed-horizon results excluded; selection bias possible.",
            "Cost 2 percentage points is a stress assumption, not measured all-in cost.",
            "Bounded quote return cannot go below -100%; actual loss plus fees can differ.",
            "Snapshot is selected at/before signal, not guaranteed that the strategy consumed it then.",
            "No threshold validated on new prospective shadow paper trades.",
            "A positive training or test mean on this tiny set does not justify live capital."
        ],
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--v32",default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    p.add_argument("--db",default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    p.add_argument("--output",default="research_v32_profitability_audit.json")
    p.add_argument("--train-share",type=float,default=0.6)
    a=p.parse_args()
    if not 0.50<=a.train_share<=0.80:
        p.error("train-share must be between 0.5 and 0.8")
    state=json.loads(Path(a.v32).read_text())
    with sqlite3.connect(Path(a.db).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        result=report(state,conn,a.train_share)
    target=Path(a.output)
    tmp=target.with_name(target.name+".tmp")
    tmp.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    tmp.replace(target)
    print("=== DATA QUALITY ===")
    print(json.dumps(result.get("counts",result),indent=2,ensure_ascii=False))
    if result.get("baseline"):
        print("=== BASELINE ===")
        print(json.dumps(result["baseline"],indent=2,ensure_ascii=False))
        print("=== BEST TRAINING FILTER (UNVALIDATED) ===")
        print(json.dumps(result["best_training_rule"],indent=2,ensure_ascii=False))
        print("=== TOP FILTERS TRAIN-SELECTED, TEST EVALUATED ===")
        for item in result["candidate_rules_top10"]:
            print(str(item["rule"]), "train",item["train"]["n"],
                  item["train"]["mean_pct"],"test",item["test"]["n"],
                  item["test"]["mean_pct"],"test_sufficient",item["test_sufficient"])
    print("Report saved:",target)
    print("NO LIVE ORDERS; NO AUTOMATIC FILTER CHANGES.")


if __name__=="__main__":
    main()
