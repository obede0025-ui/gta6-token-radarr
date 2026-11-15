"""Strictly prior-signal pair-age threshold sensitivity, read-only research.

Tests a fixed set of possible age cutoffs on existing Jupiter paper quotes.
No optimization, trading, API requests, or DB/state writes.
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from research_jupiter_minute_quotes import STATE, dt
from research_jupiter_minute_evaluation import evaluate
from research_pre_signal_risk import MINUTE, ORIGINAL, ro

THRESHOLDS_MINUTES=(10,15,20,30,45,60)
LOOKBACK_SECONDS=600

def strictly_prior(db,table,mint,pair,signal):
    """Return only an exact pair sample strictly BEFORE the signal timestamp."""
    if signal is None or not mint or not pair:return None
    if table=="minute_observations":
        sql="""SELECT observed_at,status,liquidity_usd,volume_m5,
                 buys_m5,sells_m5,pair_age_hours,price_usd
                 FROM minute_observations
                 WHERE mint=? AND pair=? AND observed_at<?
                 ORDER BY observed_at DESC LIMIT 1"""
        keys=("status","liquidity_usd","volume_m5","buys_m5",
              "sells_m5","pair_age_hours","price_usd")
    elif table=="snapshots":
        sql="""SELECT scanned_at,liquidity_usd,volume_m5,pair_age_hours
                 FROM snapshots
                 WHERE token_address=? AND pair_address=? AND scanned_at<?
                 ORDER BY scanned_at DESC LIMIT 1"""
        keys=("liquidity_usd","volume_m5","pair_age_hours")
    else:raise ValueError("Unexpected table")
    raw=db.execute(sql,(mint,pair,signal.isoformat(timespec="seconds"))).fetchone()
    if raw is None:return None
    recorded=dt(raw[0])
    if recorded is None:return None
    lag=(signal-recorded).total_seconds()
    if not 0<lag<=LOOKBACK_SECONDS:return None
    return {"recorded_utc":raw[0],"lag_seconds":round(lag,2),
            **dict(zip(keys,raw[1:]))}

def summarize(records,thresholds=THRESHOLDS_MINUTES):
    passing=[r for r in records if r["gate"]=="PASS_ROUNDTRIP_AT_MOST_3_PERCENT"]
    def metrics(rows):
        outcomes={label:sum(r["outcome"]==label for r in rows)
                  for label in ("TP_QUOTE_OBSERVED","SL_QUOTE_OBSERVED",
                                "CENSORED_BEFORE_HORIZON")}
        observed=sorted(r["stress_quote_pct"] for r in rows
                        if r["stress_quote_pct"] is not None)
        return {"n":len(rows),**outcomes,
                "observed_n":len(observed),
                "observed_mean_stress_pct":round(sum(observed)/len(observed),4) if observed else None,
                "observed_median_stress_pct":round((observed[(len(observed)-1)//2]+
                    observed[len(observed)//2])/2,4) if observed else None,
                "observed_worst_stress_pct":observed[0] if observed else None}
    baseline=metrics(passing)
    valid=[r for r in passing if r["age_minutes"] is not None]
    missing=[r for r in passing if r["age_minutes"] is None]
    results=[]
    for t in thresholds:
        kept=[r for r in valid if r["age_minutes"]>=t]
        excluded=[r for r in valid if r["age_minutes"]<t]
        results.append({"minimum_age_minutes":t,
                        "kept":metrics(kept),
                        "excluded_by_age":metrics(excluded),
                        "unknown_age_not_decided":metrics(missing)})
    return {"baseline_gate_passing":baseline,
            "valid_pre_signal_age":len(valid),"missing_pre_signal_age":len(missing),
            "age_threshold_sensitivity":results}

def report(state,minute_path=MINUTE,original_path=ORIGINAL):
    if state.get("mode")!="JUPITER_MINUTE_QUOTE_ONLY":raise ValueError("Invalid quote state")
    records=[]
    with ro(minute_path) as minute,ro(original_path) as original:
        for position in state.get("positions",{}).values():
            if not isinstance(position,dict):continue
            signal=dt(position.get("signal_utc"))
            mint=position.get("mint");pair=position.get("pair")
            m=strictly_prior(minute,"minute_observations",mint,pair,signal)
            o=strictly_prior(original,"snapshots",mint,pair,signal)
            # Use the latest strictly prior exact-pair sample, prioritizing
            # minute on equal timestamps; unknown age must stay unknown.
            candidates=[(s["recorded_utc"],rank,src,s)
                        for rank,src,s in ((1,"minute",m),(0,"original",o)) if s]
            chosen=max(candidates,key=lambda row:(row[0],row[1])) if candidates else None
            source,market=(chosen[2],chosen[3]) if chosen else (None,None)
            age=market.get("pair_age_hours") if market else None
            age_minutes=(round(age*60,4) if isinstance(age,(float,int)) and
                not isinstance(age,bool) and 0<=age<=1e6 else None)
            q=evaluate(position)
            records.append({"mint":mint,"signal_utc":position.get("signal_utc"),
                            "gate":q["gate"],"outcome":q["outcome"],
                            "stress_quote_pct":q.get("stress_quote_pct"),
                            "source":source,"sample_lag_seconds":market["lag_seconds"] if market else None,
                            "age_minutes":age_minutes,
                            "liquidity_usd":market.get("liquidity_usd") if market else None,
                            "volume_m5":market.get("volume_m5") if market else None,
                            "buys_m5":market.get("buys_m5") if market else None,
                            "sells_m5":market.get("sells_m5") if market else None})
    sensitivity=summarize(records)
    losses=[r for r in records if r["gate"]=="PASS_ROUNDTRIP_AT_MOST_3_PERCENT"
            and r["outcome"]=="SL_QUOTE_OBSERVED"]
    return {"mode":"STRICT_PRE_SIGNAL_PAIR_AGE_SENSITIVITY_RESEARCH_ONLY",
            "total_quote_candidates":len(records),
            "strictly_prior_coverage":{"minute":sum(r["source"]=="minute" for r in records),
                "original":sum(r["source"]=="original" for r in records),
                "unmatched":sum(r["source"] is None for r in records)},
            **sensitivity,
            "passing_sl_cases":losses,
            "limitations":[
                "Exact mint+pair only; market timestamp must be strictly earlier than signal.",
                "Age is the age observed in the earlier snapshot (conservative for threshold tests).",
                "Missing pair age is never counted as passing or failing an age threshold.",
                "The Jupiter router may trade a different pool than this exact pair.",
                "Thresholds use a single small historical sample and are NOT validated or optimized.",
                "Observed quotes are not executable fills, guaranteed stop-losses or realized P&L.",
                "No writes, orders, additional API requests or active selection changes."
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",default=STATE)
    p.add_argument("--minute",default=MINUTE)
    p.add_argument("--original",default=ORIGINAL)
    args=p.parse_args()
    print(json.dumps(report(json.loads(Path(args.state).read_text()),
                            args.minute,args.original),indent=2))

if __name__=="__main__":main()
