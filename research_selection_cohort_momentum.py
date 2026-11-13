"""Forward-only comparison of identical B3 research predicates across two cohorts.

Uses contemporaneous exact-pair snapshots saved by selection_shadow.
No orders, quotes, trading approvals, replay-based positions or API requests.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_b3_minute_safety_shadow import assess
from research_b3_minute_momentum_validation import select_causal, momentum

DEFAULT="/home/ubuntu/gta6-v32-gate-pilot/selection_shadow_comparison.sqlite"

def analyze(rows):
    cohorts={}
    for cohort in ("legacy","proposed"):
        items=[r for r in rows if r[1]==cohort]
        items.sort(key=lambda r:(r[0],r[2],r[3]))
        history=defaultdict(list)
        seen=set()
        counts=Counter()
        mints=set()
        pairs=set()
        first_signals=[]
        for observed,_,mint,pair,status,price,liq,vol,buys,sells,age in items:
            mints.add(mint)
            pairs.add((mint,pair))
            counts["observations"]+=1
            if status=="LIQUIDITY_UNKNOWN":counts["liquidity_unknown"]+=1
            if status=="MARKET_DATA_UNAVAILABLE":counts["market_unavailable"]+=1
            base_row=(observed,mint,pair,observed,status,price,liq,vol,buys,sells,age)
            base=assess(base_row)["decision"]
            current=(observed,status,price,vol)
            key=(mint,pair)
            # Positive momentum history accepts only exact-pair known-liquidity samples.
            if status=="QUOTE_MARKET_OBSERVED" and price is not None and price>0:
                chosen=select_causal(history[key],current,"spaced")
                if chosen is not None:
                    counts["spaced_history_ready"]+=1
                if base=="BASE_FILTER_PASS_MOMENTUM_UNVERIFIED":
                    counts["base_pass_with_age"]+=1
                    if chosen is not None:
                        counts["base_with_history_ready"]+=1
                        if momentum(current,chosen) is True:
                            counts["base_plus_spaced_momentum_matches"]+=1
                            if key not in seen:
                                seen.add(key)
                                first_signals.append({"observed_at":observed,"mint":mint,"pair":pair,
                                                      "price_usd":price})
                history[key].append(current)
        cohorts[cohort]={
            "observations":counts["observations"],
            "unique_mints":len(mints),
            "unique_pairs":len(pairs),
            "liquidity_unknown":counts["liquidity_unknown"],
            "market_unavailable":counts["market_unavailable"],
            "base_pass_with_age":counts["base_pass_with_age"],
            "base_with_history_ready":counts["base_with_history_ready"],
            "base_plus_spaced_momentum_matches":counts["base_plus_spaced_momentum_matches"],
            "first_unique_research_signals":len(first_signals),
            "signals":first_signals[:10],
            "all_signal_pairs":set(seen),
        }
    old=cohorts["legacy"];new=cohorts["proposed"]
    overlap=old["all_signal_pairs"] & new["all_signal_pairs"]
    summary={
        "unique_signal_pair_delta":new["first_unique_research_signals"]-old["first_unique_research_signals"],
        "overlapping_signal_pairs":len(overlap),
        "proposed_only_signal_pairs":len(new["all_signal_pairs"]-old["all_signal_pairs"]),
        "legacy_only_signal_pairs":len(old["all_signal_pairs"]-new["all_signal_pairs"]),
    }
    for x in cohorts.values():x.pop("all_signal_pairs")
    return {"mode":"PROSPECTIVE_SELECTION_COHORT_MOMENTUM_RESEARCH",
            "cohorts":cohorts,"comparison":summary,
            "verdict":"INSUFFICIENT_EVIDENCE_NOT_A_TRADING_COMPARISON",
            "limitations":[
                "Only snapshots collected after cohort logging was enabled are eligible.",
                "Overlapping pairs across cohorts are not independent.",
                "First signals are causal spaced momentum matches, NOT actual B3 approvals.",
                "Missing age/liquidity blocks a candidate; unknown is not zero.",
                "No Jupiter execution, sellability, realized P&L or future profitability inferred."
            ]}

def report(path=DEFAULT,hours=24,now=None):
    now=now or datetime.now(timezone.utc)
    if not Path(path).exists():
        return {"mode":"PROSPECTIVE_SELECTION_COHORT_MOMENTUM_RESEARCH",
                "verdict":"WAITING_FOR_FIRST_SAMPLE","cohorts":{}}
    cutoff=(now-timedelta(hours=hours)).isoformat(timespec="seconds")
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='selection_cohort_samples'").fetchone()
        if not exists:
            return {"mode":"PROSPECTIVE_SELECTION_COHORT_MOMENTUM_RESEARCH",
                    "verdict":"WAITING_FOR_FIRST_SAMPLE","cohorts":{}}
        rows=db.execute("""SELECT observed_at,cohort,mint,pair,status,price_usd,
                            liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
                            FROM selection_cohort_samples WHERE observed_at>=?
                            ORDER BY observed_at,cohort,mint,pair""",(cutoff,)).fetchall()
    if not rows:
        return {"mode":"PROSPECTIVE_SELECTION_COHORT_MOMENTUM_RESEARCH",
                "verdict":"WAITING_FOR_FIRST_SAMPLE","cohorts":{}}
    result=analyze(rows)
    result["window_hours"]=hours
    result["first_sample_utc"]=rows[0][0]
    result["last_sample_utc"]=rows[-1][0]
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--path",default=DEFAULT)
    p.add_argument("--hours",type=int,default=24)
    a=p.parse_args()
    print(json.dumps(report(a.path,a.hours),indent=2))

if __name__=="__main__":main()
