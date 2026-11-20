"""Frozen, read-only weighted momentum shadow comparison.

Exploratory rules chosen before any *new* forward evaluation. Historical data
has already been inspected; this is NOT an out-of-sample validation.
"""
import argparse
import json
import sqlite3
from bisect import bisect_left
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from research_liquidity_spike_classifier import classify, finite, utc

LAG_SECONDS=60
HORIZONS=(60,180,300)

def buy_share(buys,sells):
    b,s=finite(buys),finite(sells)
    return b/(b+s) if b is not None and s is not None and b>=0 and s>=0 and b+s>0 else None

def pct(start,end):
    a,b=finite(start),finite(end)
    return 100*(b/a-1) if a is not None and b is not None and a>0 else None

def market_eligible(r):
    _,_,_,price,liq,vol,b,s,age=r
    p,l,v,b,s,a=map(finite,(price,liq,vol,b,s,age))
    return bool(p is not None and p>0 and l is not None and l>=20000
        and v is not None and v>=250 and b is not None and s is not None
        and b>s and a is not None and 0<=a<=24)

def next_mark(series,target):
    timestamps=[x[0] for x in series]
    j=bisect_left(timestamps,target)
    return (series[j] if j<len(series) and
            0 <= (series[j][0]-target).total_seconds() <= LAG_SECONDS else None)

def features(base,m1,m3):
    """Differences of baseline-relative 1m and 3m changes, available at 3m."""
    p0,l0,b0,s0=base[3],base[4],base[6],base[7]
    p1,l1,b1,s1=m1[3],m1[4],m1[6],m1[7]
    p3,l3,b3,s3=m3[3],m3[4],m3[6],m3[7]
    pp1,pp3=pct(p0,p1),pct(p0,p3)
    ll1,ll3=pct(l0,l1),pct(l0,l3)
    bs1,bs3=buy_share(b1,s1),buy_share(b3,s3)
    if None in (pp1,pp3,ll1,ll3,bs1,bs3):
        return None
    return {"price_accel_pp":pp3-pp1,
            "liquidity_accel_pp":ll3-ll1,
            "buy_share_accel_pp":100*(bs3-bs1),
            "liquidity_at_decision":finite(l3),
            "liquidity_at_signal":finite(l0),
            "price_at_decision":finite(p3)}

def score(feat,weights):
    def cap(x):return max(-2,min(2,x))
    # Scales fixed before this run: price 5pp; liquidity 5pp; buy-share 2pp.
    return sum(weights[k]*cap(feat[k]/scale) for k,scale in (
       ("price_accel_pp",5),("liquidity_accel_pp",5),
       ("buy_share_accel_pp",2)))

def decisions(feat):
    # Separate precautionary shadow gate at t+3m. No real trade approval.
    l0,l3=feat["liquidity_at_signal"],feat["liquidity_at_decision"]
    risk=(l0 is None or l3 is None or l3<20000 or l3/l0<=0.2)
    strict=all(feat[k]>0 for k in
       ("price_accel_pp","liquidity_accel_pp","buy_share_accel_pp"))
    balanced=score(feat,dict(price_accel_pp=.4,liquidity_accel_pp=.4,
                              buy_share_accel_pp=.2))>=.35
    price_focused=score(feat,dict(price_accel_pp=.6,liquidity_accel_pp=.2,
                                   buy_share_accel_pp=.2))>=.35
    return {"strict_all_positive":strict,"balanced_fixed":balanced,
            "price_focused_fixed":price_focused,
            "liquidity_risk_at_3m":risk}

def read_minute(db,begin,end):
    grouped=defaultdict(list)
    candidates=[]
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows=conn.execute("""SELECT observed_at,mint,pair,price_usd,liquidity_usd,
                   volume_m5,buys_m5,sells_m5,pair_age_hours
                   FROM minute_observations
                   WHERE status='QUOTE_MARKET_OBSERVED'
                     AND julianday(observed_at)>=julianday(?)
                     AND julianday(observed_at)<=julianday(?)
                   ORDER BY julianday(observed_at),mint,pair""",
                 (begin.isoformat(),end.isoformat()))
        for raw in rows:
            t=utc(raw[0])
            if t is None or not raw[1] or not raw[2]:continue
            r=(t,*raw[1:])
            grouped[(raw[1],raw[2])].append(r)
            if market_eligible(r):candidates.append(r)
    return grouped,candidates

def analyze(db,hours=168,as_of=None):
    now=as_of or datetime.now(timezone.utc)
    if now.tzinfo is None or not 1<=hours<=744:raise ValueError("invalid hours/clock")
    grouped,candidates=read_minute(db,now-timedelta(hours=hours),now)
    picked={}
    # Chronological order across all trading pairs. First qualifying pair per mint.
    for r in candidates:
        picked.setdefault(r[1],r)
    records=[]
    excluded=Counter()
    for mint,base in picked.items():
        t,pair=base[0],base[2]
        series=grouped[(mint,pair)]
        marks={h:next_mark(series,t+timedelta(seconds=h)) for h in HORIZONS}
        if any(marks[h] is None for h in HORIZONS):
            excluded["MISSING_1_3_OR_5M"]+=1
            continue
        feat=features(base,marks[60],marks[180])
        if feat is None:
            excluded["INVALID_1_3M_FEATURE"]+=1
            continue
        label,_=classify(base[3],marks[300][3],base[4],marks[300][4],False)
        if label=="INSUFFICIENT_MARKET_EVIDENCE":
            excluded["INVALID_5M_OUTCOME"]+=1
            continue
        d=decisions(feat)
        records.append({"mint":mint,"pair":pair,"baseline_utc":t.isoformat(),
            "decision_utc":marks[180][0].isoformat(),"outcome_utc":marks[300][0].isoformat(),
            "features":{k:round(v,4) if v is not None else None for k,v in feat.items()},
            "decision":d,"outcome_label":label,
            "baseline_to_5m_pct":round(pct(base[3],marks[300][3]),4),
            "decision_to_5m_pct":round(pct(marks[180][3],marks[300][3]),4),
            "horizon_5m_liquidity_usd":finite(marks[300][4]),
            "quote_status":"NOT_AVAILABLE_IN_MINUTE_DB"})
    labels=Counter(r["outcome_label"] for r in records)
    summary={}
    for method in ("strict_all_positive","balanced_fixed","price_focused_fixed"):
        c=Counter()
        for r in records:
            # Risk is reported separately, NOT combined into the momentum score.
            decision="PASS" if r["decision"][method] else "BLOCK"
            c[(decision,r["outcome_label"])]+=1
        summary[method]={f"{dec}:{lab}":count for (dec,lab),count in sorted(c.items())}
        summary[method]["pass_total"]=sum(n for (d,_),n in c.items() if d=="PASS")
        summary[method]["pass_qualified_momentum"]=c[("PASS","MOMENTUM_WITH_LIQUIDITY")]
        summary[method]["missed_qualified_momentum"]=c[("BLOCK","MOMENTUM_WITH_LIQUIDITY")]
    risk=Counter((str(r["decision"]["liquidity_risk_at_3m"]),
                 r["outcome_label"]) for r in records)
    return {"mode":"WEIGHTED_MOMENTUM_HISTORICAL_SHADOW_READ_ONLY",
        "checked_utc":now.isoformat(),"hours":hours,"unique_first_eligible_mints":len(picked),
        "evaluated_mints":len(records),"excluded":dict(excluded),"outcomes":dict(labels),
        "score_comparison":summary,"separate_liquidity_risk":{f"{a}:{b}":v for (a,b),v in risk.items()},
        "records":records,"api_calls":0,"db_writes":0,"orders_executed":0,
        "warnings":["Historical dataset already inspected; all comparisons are exploratory, not holdout.",
          "One first eligible exact pair per token, regardless of whether it later gains.",
          "Scoring information at 3m cannot support trading claims from the original signal.",
          "Decision-to-5m change is a non-executable mark; Jupiter execution quotes absent.",
          "Liquidity warning does not get overridden by momentum score.",
          "Hold weights fixed and test on newly collected, future observation cohorts."]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",required=True)
    p.add_argument("--hours",type=int,default=168)
    a=p.parse_args()
    r=analyze(a.db,a.hours)
    print(json.dumps({k:v for k,v in r.items() if k!="records"},indent=2))
    print("SAMPLE_RECORDS")
    print(json.dumps(r["records"][:5],indent=2))

if __name__=="__main__":main()
