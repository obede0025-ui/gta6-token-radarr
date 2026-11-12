"""Read-only B3 momentum equivalence study for minute pilot.

No state writes, orders, or trade approvals. Compares the LEGACY price/volume
predicate with time-spaced causal sampling, never uses future observations.
Legacy B3 signal_b uses two prior valid samples with consecutive gaps 3.5-20m,
current / older >=1.05, current > previous and non-null volumes.
This study deliberately reports comparable mechanics, NOT execution performance.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from research_b3_minute_safety_shadow import assess, num

DEFAULT_DB="/home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite"
MIN_GAP=3.5
MAX_GAP=20.0

def minutes(later,earlier):
    return (datetime.fromisoformat(later)-datetime.fromisoformat(earlier)).total_seconds()/60

def select_causal(history,current,mode):
    """Return two preceding snapshots, without lookahead.

    mode=raw: emulate original nearest-two behavior (minute gaps disqualify).
    mode=spaced: use nearest observations *meeting* required 3.5-20m gaps.
    BOTH use the unchanged B3 >=5% momentum price predicate.
    """
    if mode not in ("raw","spaced"):raise ValueError("Invalid sampling mode")
    past=[r for r in history if r[0]<current[0] and r[2] is not None and num(r[2]) is not None and num(r[2])>0]
    past.sort(key=lambda r:r[0],reverse=True)
    if mode=="raw":
        if len(past)<2:return None
        previous,older=past[:2]
        return (older,previous) if MIN_GAP<=minutes(current[0],previous[0])<=MAX_GAP and MIN_GAP<=minutes(previous[0],older[0])<=MAX_GAP else None
    previous=next((r for r in past if MIN_GAP<=minutes(current[0],r[0])<=MAX_GAP),None)
    if previous is None:return None
    older=next((r for r in past if r[0]<previous[0] and MIN_GAP<=minutes(previous[0],r[0])<=MAX_GAP),None)
    return (older,previous) if older is not None else None

def momentum(current,chosen):
    if chosen is None:return None
    older,previous=chosen
    price=num(current[2]);vol=num(current[3])
    old_price=num(older[2]);prev_price=num(previous[2])
    v1=num(older[3]);v2=num(previous[3])
    if price is None or price<=0 or vol is None or vol<250:return False
    if old_price is None or old_price<=0 or prev_price is None or prev_price<=0:return False
    if v1 is None or v2 is None:return False
    return price/old_price>=1.05 and price>prev_price

def analyze(rows):
    history=defaultdict(list)
    count=Counter()
    examples=[]
    for row in rows:
        stamp,mint,pair,source,status,price,liq,vol,buys,sells,age=row
        # Maintain distinct histories by exact mint and pinned pair.
        current=(stamp,status,price,vol)
        key=(mint,pair)
        base=assess(row)["decision"]
        count["observations"]+=1
        if base=="BASE_FILTER_PASS_MOMENTUM_UNVERIFIED":
            count["base_pass"]+=1
        elif base=="LIQUIDITY_UNKNOWN":count["unknown_liquidity"]+=1
        elif base=="BASE_FILTER_BLOCKED":count["base_blocked"]+=1
        else:count["market_unavailable"]+=1
        if status=="QUOTE_MARKET_OBSERVED" and num(price) is not None and num(price)>0:
            count["valid_market_samples"]+=1
            old=select_causal(history[key],current,"raw")
            spaced=select_causal(history[key],current,"spaced")
            raw_signal=momentum(current,old)
            spaced_signal=momentum(current,spaced)
            if old is not None:count["legacy_spacing_ready"]+=1
            if spaced is not None:count["spaced_sampling_ready"]+=1
            if raw_signal is True:count["legacy_price_momentum"]+=1
            if spaced_signal is True:count["spaced_price_momentum"]+=1
            if spaced_signal is True and base=="BASE_FILTER_PASS_MOMENTUM_UNVERIFIED":
                count["base_plus_spaced_momentum_RESEARCH_ONLY"]+=1
                if len(examples)<5:
                    examples.append({"observed_at":stamp,"mint_prefix":mint[:12],"pair_prefix":pair[:12],
                                     "older_utc":spaced[0][0],"previous_utc":spaced[1][0]})
            # invalid liquidity samples still carry a market price for research
            history[key].append(current)
        elif status=="LIQUIDITY_UNKNOWN" and num(price) is not None and num(price)>0:
            # Price may exist without liquidity. Keep it OUT of the
            # momentum baseline so it never contaminates positive research.
            count["priced_but_liquidity_unknown_excluded_from_momentum"]+=1
    return {"mode":"READ_ONLY_B3_MINUTE_MOMENTUM_VALIDATION",
            "summary":dict(count),
            "research_examples_not_signals":examples,
            "rules":{"min_gap_minutes":MIN_GAP,"max_gap_minutes":MAX_GAP,
                     "price_ratio_minimum":1.05,"current_price_above_previous":True,
                     "min_current_volume_usd":250,
                     "liquidity_minimum_usd":20000},
            "interpretation":[
                "Raw = immediately prior two market samples under legacy 3.5-20m spacing.",
                "Spaced = causal subselection of samples 3.5-20m apart; NOT validated B3 equivalence.",
                "Existing B3 safety filters are required for any combined research count.",
                "Unknown liquidity observations are never entry-eligible and excluded from momentum history.",
                "This diagnostic does not issue approvals, paper trades or execution quotes.",
                "Small and selected 30-token cohorts cannot establish strategy performance."
            ],
            "no_database_or_state_writes":True}

def run(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        rows=db.execute("""
            SELECT observed_at,mint,pair,source_snapshot_at,status,
                   price_usd,liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
            FROM minute_observations ORDER BY observed_at,mint,pair
        """).fetchall()
    return analyze(rows)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=DEFAULT_DB)
    args=p.parse_args()
    print(json.dumps(run(args.db),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
