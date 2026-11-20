"""Conservative, chronological research simulation on existing minute observations.

No quote data, fills, live trades, network requests or source writes. Parameters
are selected on early data only; later data is never used to choose a model.
Historical dates have been inspected already: the final split is a pseudo-holdout,
not an untouched scientific validation. Do not claim proven profitability.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median, mean

from research_weighted_momentum_shadow import analyze as minute_analyze
from research_weighted_momentum_shadow import score
from research_liquidity_spike_classifier import utc

EMBARGO_SECONDS=1200
MIN_TRAIN_PASSES=10
MIN_TRAIN_RATE=.08
COST_SCENARIOS_PCT=(4.0,8.0,12.0)
METHODS={
    "strict":None,
    "balanced_0.0":(.4,.4,.2,0.0),
    "balanced_0.35":(.4,.4,.2,.35),
    "balanced_0.7":(.4,.4,.2,.7),
    "price_0.0":(.6,.2,.2,0.0),
    "price_0.35":(.6,.2,.2,.35),
    "price_0.7":(.6,.2,.2,.7),
}
RISK_LABELS={"LIQUIDITY_COLLAPSE_WITH_PRICE_LOSS",
             "MOMENTUM_LIQUIDITY_COLLAPSE"}

def qualify(record,method):
    feature=record["features"]
    if record["decision"]["liquidity_risk_at_3m"]:
        return False
    if method=="strict":
        return all(feature[k]>0 for k in
            ("price_accel_pp","liquidity_accel_pp","buy_share_accel_pp"))
    weights=METHODS[method]
    if weights is None:
        raise ValueError("unknown model")
    p,l,b,threshold=weights
    return score(feature,{"price_accel_pp":p,
                          "liquidity_accel_pp":l,
                          "buy_share_accel_pp":b})>=threshold

def scenario_return(r,cost_pct):
    """Research-only haircut: liquidation failure stress, otherwise price mark less cost.

    A 100% loss for a price+LP collapse is deliberately pessimistic; not a
    measured filled exit. Non-collapsed marks are likewise NOT executable.
    """
    lp=r.get("horizon_5m_liquidity_usd")
    observed=r.get("decision_to_5m_pct")
    if observed is None or lp is None:
        return -100.0
    if r["outcome_label"] in RISK_LABELS or lp<20000:
        return -100.0
    return max(-100.0,observed-cost_pct)

def summarize(records,method,cost_pct):
    picked=[r for r in records if qualify(r,method)]
    rets=[scenario_return(r,cost_pct) for r in picked]
    if not rets:
        return {"total_tokens":len(records),"passed":0,
                "pass_pct":0,"mean_stress_pct":None,"median_stress_pct":None,
                "positive_stress_pct":None,"worst_stress_pct":None,
                "collapse_after_pass":0}
    return {"total_tokens":len(records),"passed":len(picked),
            "pass_pct":round(100*len(picked)/len(records),2) if records else 0,
            "mean_stress_pct":round(mean(rets),3),
            "median_stress_pct":round(median(rets),3),
            "positive_stress_pct":round(100*sum(x>0 for x in rets)/len(rets),2),
            "worst_stress_pct":round(min(rets),3),
            "collapse_after_pass":sum(r["outcome_label"] in RISK_LABELS
                                       for r in picked)}

def chronosplit(records):
    """Chronological 60/20/20 split with 20-minute gap at each boundary."""
    ordered=sorted(records,key=lambda r:(utc(r["baseline_utc"]),r["mint"]))
    if len(ordered)<30:
        return {"train":[],"validation":[],"pseudo_test":[]}, {"too_few_records":len(ordered)}
    n=len(ordered)
    pivot1=utc(ordered[int(n*.6)]["baseline_utc"])
    pivot2=utc(ordered[int(n*.8)]["baseline_utc"])
    gap=timedelta(seconds=EMBARGO_SECONDS)
    cohorts={
        "train":[r for r in ordered if utc(r["baseline_utc"])<pivot1-gap],
        "validation":[r for r in ordered
                      if pivot1+gap<=utc(r["baseline_utc"])<pivot2-gap],
        "pseudo_test":[r for r in ordered if utc(r["baseline_utc"])>=pivot2+gap],
    }
    return cohorts,{"pivot_train_validation_utc":pivot1.isoformat(),
                    "pivot_validation_test_utc":pivot2.isoformat(),
                    "embargo_seconds":EMBARGO_SECONDS,
                    "discarded_boundary_tokens":n-sum(map(len,cohorts.values()))}

def select_on_training(train):
    candidates=[]
    for name in METHODS:
        s=summarize(train,name,4.0)
        if s["passed"]<MIN_TRAIN_PASSES or s["pass_pct"]<MIN_TRAIN_RATE*100:
            continue
        # Risk priority: no pass collapses on train if possible; maximize
        # conservative mean; 3 decimal precision is not statistical evidence.
        candidates.append((s["collapse_after_pass"],
                           -s["mean_stress_pct"],
                           -s["positive_stress_pct"],name))
    if not candidates:return None
    return sorted(candidates)[0][-1]

def simulate_records(records):
    # Never choose weights using validation or test.
    groups,boundaries=chronosplit(records)
    train,valid,test=(groups[k] for k in ("train","validation","pseudo_test"))
    chosen=select_on_training(train)
    outputs={}
    for method in METHODS:
        outputs[method]={cohort: summarize(rows,method,4.0)
            for cohort,rows in groups.items()}
    validation_result=outputs[chosen]["validation"] if chosen else None
    # Registered acceptance threshold, not trained/tuned after looking at test:
    # at least 10 distinct validation tokens, mean stress >0, positive >50%,
    # and zero observed LP collapses among validation passes.
    validation_acceptable=bool(
        chosen and validation_result["passed"]>=10
        and validation_result["mean_stress_pct"]>0
        and validation_result["positive_stress_pct"]>50
        and validation_result["collapse_after_pass"]==0)
    return {"total_records":len(records),
            "cohort_sizes":{k:len(v) for k,v in groups.items()},
            "boundaries":boundaries,
            "selection_method":"training only, risk-first then mean stressed return; no tuning on validation/test",
            "training_chosen":chosen,
            "validation_thresholds_met":validation_acceptable,
            "evaluation_status":"HISTORICAL_EXPLORATORY_ONLY_NOT_VALIDATED"
                if chosen else "INSUFFICIENT_TRAINING_CANDIDATES",
            "all_methods_price_marks_4pct_cost":outputs,
            "chosen_stress_scenarios":{
                str(cost):{k:summarize(v,chosen,cost) for k,v in groups.items()}
                for cost in COST_SCENARIOS_PCT
            } if chosen else {},
            "warnings":[
                "The full 7-day historical data were already inspected, so even the last 20% is only a pseudo-holdout.",
                "One earliest market-qualified mark per token; not a Strategy A signal cohort.",
                "Outcomes are 5m from baseline but returns are measured from 3m decision mark.",
                "Missing real Jupiter buy/sell routes and actual trade fills; no proven edge or live trade approval.",
                "4/8/12% hypothetical all-in friction scenarios are sensitivity assumptions, not measured costs.",
                "Liquidation collapse stress assumes -100%; cannot infer exact loss from marks.",
                "Do not tune rules after viewing validation/test and call the same period out of sample.",
                "Even favorable retrospective numbers require fresh prospective quote-only validation."
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--hours",type=int,default=168)
    a=p.parse_args()
    research=minute_analyze(a.db,a.hours)
    result=simulate_records(research["records"])
    print(json.dumps({"mode":"CHRONOLOGICAL_MARKET_SIMULATOR_READ_ONLY",
       "checked_utc":datetime.now(timezone.utc).isoformat(),
       "source_unique_tokens":research["unique_first_eligible_mints"],
       "records_with_followups":research["evaluated_mints"],
       **result,"api_calls":0,"orders_executed":0,"db_writes":0},indent=2))
if __name__=="__main__":main()
