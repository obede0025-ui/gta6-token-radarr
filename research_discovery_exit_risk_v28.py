"""V2.8 read-only exit-risk audit of V2.7 first signals and route quotes.

Compares PREDECLARED fixed 1/5/15-minute sell horizons on exactly the
same first-signal population. No per-token hindsight exit optimization.
Studies source-snapshot metrics known at signal time. No orders or HTTP.
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean,median

from research_discovery_quote_v27 import SOURCE,OUTCOMES,BUY_USDC_RAW

HORIZONS=(60,300,900)
ROUTE_OK="JUPITER_SELL_ROUTE_QUOTED"

def read_ro(path):
    if not Path(path).is_file():return None
    db=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)
    db.execute("PRAGMA query_only=ON")
    return db

def source_at_signal(db,mint,pair,stamp):
    """Metadata from signal timestamp ONLY, never later market metrics."""
    if db is None:return None
    rows=db.execute("""SELECT volume_m5,buys_m5,sells_m5,liquidity_usd,
         pair_age_hours,price_usd
         FROM quality_cohort_samples
         WHERE mint=? AND pair=? AND observed_at=? AND momentum_pass=1
         ORDER BY cohort""",(mint,pair,stamp)).fetchall()
    if not rows:return None
    vol,buy,sell,liq,age,price=rows[0]
    share=(buy/(buy+sell)*100 if buy is not None and sell is not None
           and buy+sell>0 else None)
    return {"volume_m5":vol,"buys_m5":buy,"sells_m5":sell,
            "buy_share_pct":round(share,2) if share is not None else None,
            "liquidity_usd":liq,"pair_age_hours":age,"price_usd":price}

def summary(returns,total):
    values=[v for v in returns if v is not None]
    return {"signal_count":total,"valid_quotes":len(values),
            "unverified":total-len(values),
            "positive":sum(x>0 for x in values),
            "negative":sum(x<0 for x in values),
            "mean_pct":round(mean(values),3) if values else None,
            "median_pct":round(median(values),3) if values else None,
            "worst_pct":round(min(values),3) if values else None,
            "best_pct":round(max(values),3) if values else None,
            "sum_equal_usdc_stakes_return_pct":round(sum(values),3) if values else None}

def audit(source=SOURCE,outcomes=OUTCOMES,examples=8):
    if not 0<=examples<=30:raise ValueError("examples must be 0..30")
    db=read_ro(outcomes)
    if db is None:
        return {"mode":"DISCOVERY_V28_EXIT_RISK","status":"NO_QUOTE_DATABASE"}
    src=read_ro(source)
    try:
        signals=db.execute("""SELECT mint,pair,signal_utc,cohorts,
            entry_liquidity_usd,entry_sell_usdc_raw,buy_status
            FROM first_signals ORDER BY signal_utc,mint""").fetchall()
        if not signals:
            return {"mode":"DISCOVERY_V28_EXIT_RISK","status":"NO_SIGNALS"}
        unique=set()
        rows=[]
        horizon_returns={h:[] for h in HORIZONS}
        coverage=Counter()
        for mint,pair,stamp,cohorts,entry_liq,initial,status in signals:
            if (mint,pair) in unique:raise ValueError("Duplicate pair in first_signals")
            unique.add((mint,pair))
            qrows=db.execute("""SELECT horizon_sec,quote_usdc_raw,status
                FROM quote_followups WHERE mint=? AND pair=?""",(mint,pair)).fetchall()
            quote_by_h={h:(raw,state) for h,raw,state in qrows}
            returns={}
            outcome_status={}
            for h in HORIZONS:
                raw,state=quote_by_h.get(h,(None,"NOT_MEASURED"))
                valid=(state==ROUTE_OK and isinstance(raw,str)
                       and raw.isdigit() and int(raw)>0)
                val=round((int(raw)/BUY_USDC_RAW-1)*100,3) if valid else None
                returns[str(h)]=val
                outcome_status[str(h)]=state
                horizon_returns[h].append(val)
                if not valid:coverage[f"{h}_{state}"]+=1
            initial_pct=(round((int(initial)/BUY_USDC_RAW-1)*100,3)
                         if initial is not None and initial.isdigit() else None)
            known=source_at_signal(src,mint,pair,stamp)
            rows.append({"mint":mint,"pair":pair,"signal_utc":stamp,
                "cohorts":cohorts,"entry_liquidity_usd":entry_liq,
                "initial_reversal_pct":initial_pct,
                "pre_signal_metrics":known,"fixed_exit_returns_pct":returns,
                "outcome_status":outcome_status})
        # A policy requiring all eight fixed exits to have a quote cannot
        # silently drop tokens with no sell route. Report coverage explicitly.
        policies={str(h):summary(horizon_returns[h],len(rows)) for h in HORIZONS}
        # Predetermined DESCRIPTIVE groups. No claim these are predictive,
        # and do not optimize cutoff on these eight outcomes.
        groups={}
        for label,predicate in (
            ("buy_share_at_least_65",lambda m:m["buy_share_pct"] is not None and m["buy_share_pct"]>=65),
            ("buy_share_below_65",lambda m:m["buy_share_pct"] is not None and m["buy_share_pct"]<65),
            ("entry_liquidity_at_least_30000",lambda m:m["liquidity_usd"] is not None and m["liquidity_usd"]>=30000),
            ("entry_liquidity_below_30000",lambda m:m["liquidity_usd"] is not None and m["liquidity_usd"]<30000)):
            group=[r for r in rows if r["pre_signal_metrics"] is not None
                   and predicate(r["pre_signal_metrics"])]
            groups[label]={"count":len(group),
                "fixed_15m":summary([r["fixed_exit_returns_pct"]["900"] for r in group],len(group))}
        ranked=sorted(rows,key=lambda r:(r["fixed_exit_returns_pct"]["900"] is None,
                 r["fixed_exit_returns_pct"]["900"] if r["fixed_exit_returns_pct"]["900"] is not None else 0))
        return {"mode":"DISCOVERY_V28_EXIT_RISK","status":"OK",
          "unique_first_signal_pairs":len(rows),
          "fixed_horizon_comparison_before_network_fees":policies,
          "predeclared_signal_time_groups":groups,
          "quote_missing_reasons":dict(coverage),
          "highest_15m_downside_cases":ranked[:examples],
          "safety":{"http_requests":0,"db_writes":0,"trades":0,"production_changes":0},
          "limitations":[
             "Fixed exit horizons compare identical first-signal population; no per-token hindsight exits.",
             "No stop-loss or take-profit intrawindow simulation: only 1/5/15m quotes exist.",
             "Overlapping token cohorts are deduplicated by exact mint and pair.",
             "Jupiter routes may use pools other than discovery pool; no executable fills.",
             "Network fees, failures, token restrictions and adverse quote movement excluded.",
             "Signal-time group comparisons are descriptive on tiny sample, not validated predictors.",
             "A missing sell quote is not zero percent return or an executable exit."
          ]}
    finally:
        db.close()
        if src is not None:src.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",default=str(SOURCE))
    p.add_argument("--outcomes",default=str(OUTCOMES))
    p.add_argument("--examples",type=int,default=5)
    a=p.parse_args()
    print(json.dumps(audit(a.source,a.outcomes,a.examples),indent=2))
if __name__=="__main__":main()
