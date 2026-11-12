"""Read-only M1/Jupiter minute quote scenario, never realized profit.

Uses the existing isolated Jupiter quote state. No API, wallet, orders or
writes to any state/database. First recorded exit quote approximates
immediate roundtrip. TP/SL are first-observed quote thresholds within 900s.
No claim of timed exit when no exit quote at/after horizon.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from research_jupiter_minute_quotes import dt, STATE

HORIZON=900
EXTRA_COST_PP=2.0
MAX_IMMEDIATE_LOSS=3.0
TP=10.0
SL=-10.0

def finite(x):
    import math
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError,OverflowError):return None

def evaluate(position):
    entry=dt(position.get("entry_quote_utc"))
    if entry is None or position.get("entry_token_raw") is None:
        return {"gate":"UNKNOWN_NO_ENTRY_QUOTE","outcome":"NO_ENTRY_QUOTE",
                "gross_quote_pct":None,"stress_quote_pct":None}
    all_valid=[]
    for q in position.get("exit_quotes",[]):
        if not isinstance(q,dict) or q.get("status")!="QUOTE_AVAILABLE":continue
        t=dt(q.get("quoted_utc"))
        pct=finite(q.get("estimated_roundtrip_pct"))
        if t is None or pct is None:continue
        age=(t-entry).total_seconds()
        if age>=0:all_valid.append((age,pct))
    all_valid.sort(key=lambda v:v[0])
    if not all_valid:
        return {"gate":"UNKNOWN_NO_IMMEDIATE_SELL_QUOTE","outcome":"NO_SELL_QUOTE",
                "gross_quote_pct":None,"stress_quote_pct":None}
    # Roundtrip gate needs a sell quote near the buy baseline. A later quote
    # cannot substitute for a measured immediate reverse quote.
    first_age,first_pct=all_valid[0]
    if first_age>60:
        gate="UNKNOWN_NO_IMMEDIATE_SELL_QUOTE"
    elif first_pct < -MAX_IMMEDIATE_LOSS:
        gate="BLOCKED_ROUNDTRIP_OVER_3_PERCENT"
    else:
        gate="PASS_ROUNDTRIP_AT_MOST_3_PERCENT"
    selected=None
    outcome="CENSORED_BEFORE_HORIZON"
    for age,pct in all_valid:
        if age>HORIZON:break
        if pct<=SL:
            selected=(age,pct);outcome="SL_QUOTE_OBSERVED";break
        if pct>=TP:
            selected=(age,pct);outcome="TP_QUOTE_OBSERVED";break
    if selected is None:
        # Time exits after 900s require a quote actually observed at/after
        # 900s, otherwise don't treat the last pre-horizon quote as fill.
        after=[v for v in all_valid if v[0]>=HORIZON]
        if after:
            selected=after[0]
            outcome=("TIME_QUOTE_AT_HORIZON" if abs(selected[0]-HORIZON)<=1
                     else "TIME_DELAYED_QUOTE")
    gross=selected[1] if selected is not None else None
    return {"gate":gate,
            "immediate_roundtrip_pct":round(first_pct,4) if first_age<=60 else None,
            "first_sell_quote_delay_seconds":round(first_age,2),
            "valid_sell_quotes":len(all_valid),
            "outcome":outcome,"exit_quote_age_seconds":round(selected[0],2) if selected else None,
            "gross_quote_pct":round(gross,4) if gross is not None else None,
            "stress_quote_pct":round(gross-EXTRA_COST_PP,4) if gross is not None else None}

def report(state):
    if state.get("mode")!="JUPITER_MINUTE_QUOTE_ONLY" or not isinstance(state.get("positions"),dict):
        raise ValueError("Unexpected quote state")
    entries=[]
    for key,p in sorted(state["positions"].items()):
        if not isinstance(p,dict):continue
        entries.append({"mint":p.get("mint"),"signal_utc":p.get("signal_utc"),
                        "entry_quote_utc":p.get("entry_quote_utc"),**evaluate(p)})
    completed=[e for e in entries if e["stress_quote_pct"] is not None]
    passing=[e for e in entries if e["gate"]=="PASS_ROUNDTRIP_AT_MOST_3_PERCENT"]
    return {"mode":"READ_ONLY_MINUTE_JUPITER_QUOTE_EVALUATION",
            "settings":{"tp_pct":TP,"sl_pct":SL,"horizon_seconds":HORIZON,
                        "max_immediate_loss_pct":MAX_IMMEDIATE_LOSS,
                        "stress_extra_cost_percentage_points":EXTRA_COST_PP},
            "summary":{"positions":len(entries),
                       "gate_counts":dict(Counter(e["gate"] for e in entries)),
                       "outcome_counts":dict(Counter(e["outcome"] for e in entries)),
                       "gate_passing":len(passing),
                       "observable_exits":len(completed),
                       "mean_stress_quote_pct_observable_only":
                          round(sum(e["stress_quote_pct"] for e in completed)/len(completed),4) if completed else None},
            "items":entries,
            "limitations":[
                "TP/SL refer to first observed quotes, not filled trades.",
                "Immediate gate is indicative and uses first reverse quote within 60 seconds.",
                "A last quote before 900 seconds is NOT a timed exit.",
                "A delayed quote after 900 seconds does not prove 15-minute execution.",
                "2 percentage points is an assumed stress buffer, not measured costs.",
                "Quote router may differ from the exact Dexscreener pair.",
                "Only observational positions are included; sample is not proof of profitability.",
                "Read-only evaluation; does not authorize live trades."
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",default=STATE)
    args=p.parse_args()
    data=json.loads(Path(args.state).read_text())
    print(json.dumps(report(data),indent=2,ensure_ascii=False))

if __name__=="__main__":main()
