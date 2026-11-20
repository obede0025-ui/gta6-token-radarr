"""Read-only audit: historical Strategy A proxies -> minute market -> prospective quotes.

Does not backdate Jupiter quotes. Past scanner signals are only research proxies.
Each signal uses precisely prior snapshots; outcomes use later minute observations.
"""
import argparse
import json
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from research_focus_a_b2 import signal_labels
from research_liquidity_spike_classifier import classify, finite, utc
from research_a_first_minutes import buy_share, change_pct

HORIZONS=(60,180,300)
TOLERANCE=60
BASE_DELAY=120
COOLDOWN=900

def market_pass(p,liq,vol,buys,sells,age):
    p,liq,vol,buys,sells,age=map(finite,(p,liq,vol,buys,sells,age))
    return bool(p is not None and p>0 and liq is not None and liq>=20000
                and vol is not None and vol>=250 and buys is not None and sells is not None
                and buys>sells and age is not None and 0<=age<=24)

def load_a_signals(db,begin,end):
    """Signal reconstruction with the same 3.5..20m history spacing as focus A proxy."""
    history=defaultdict(list)
    emitted={}
    signals=[]
    fetch_begin=begin-timedelta(hours=1)
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        rows=con.execute("""SELECT scanned_at,token_address,pair_address,price_usd,
                            liquidity_usd,volume_m5,buys_m5,sells_m5,pair_age_hours
                            FROM snapshots
                            WHERE julianday(scanned_at)>=julianday(?)
                              AND julianday(scanned_at)<=julianday(?)
                            ORDER BY julianday(scanned_at),token_address,pair_address""",
                         (fetch_begin.isoformat(),end.isoformat()))
        for stamp,mint,pair,p,liq,vol,buys,sells,age in rows:
            t=utc(stamp)
            if t is None or not mint or not pair:
                continue
            key=(mint,pair)
            h=history[key]
            if h and not 210<=(t-h[-1][0]).total_seconds()<=1200:
                h.clear()
            p=finite(p);vol=finite(vol)
            if begin<=t<=end and market_pass(p,liq,vol,buys,sells,age) and "A" in signal_labels(h,p,vol):
                last=emitted.get(key)
                if last is None or (t-last).total_seconds()>=COOLDOWN:
                    signals.append({"mint":mint,"pair":pair,"signal_utc":t.isoformat(),
                                    "strategy":"A_HISTORICAL_PROXY","scan_price_usd":p})
                    emitted[key]=t
            if p is not None and p>0:
                h.append((t,p,vol))
                if len(h)>2:h.pop(0)
            else:
                h.clear()
    return signals

def load_minute(db,begin,end):
    data=defaultdict(list)
    with sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        rows=con.execute("""SELECT observed_at,mint,pair,status,price_usd,
                            liquidity_usd,volume_m5,buys_m5,sells_m5
                            FROM minute_observations
                            WHERE julianday(observed_at)>=julianday(?)
                              AND julianday(observed_at)<=julianday(?)
                            ORDER BY mint,pair,julianday(observed_at)""",
                            (begin.isoformat(),end.isoformat()))
        for stamp,mint,pair,status,p,liq,vol,buys,sells in rows:
            t=utc(stamp)
            if t is not None and mint and pair and status=="QUOTE_MARKET_OBSERVED":
                data[(mint,pair)].append((t,finite(p),finite(liq),finite(vol),finite(buys),finite(sells)))
    return data

def next_sample(series,target,max_delay):
    ts=[x[0] for x in series]
    j=bisect_left(ts,target)
    if j>=len(series) or not 0<=(ts[j]-target).total_seconds()<=max_delay:
        return None
    return series[j]

def quote_join(cohort,mint,pair,signal_t):
    """Only prospectively captured, exact-mint/pair A records with matching time."""
    if not cohort:
        return {"status":"NO_MATCHED_PROSPECTIVE_COHORT"}
    item=cohort.get("signals",{}).get(mint+"|"+pair)
    if not isinstance(item,dict) or "A" not in item.get("strategies",[]):
        return {"status":"NO_MATCHED_PROSPECTIVE_COHORT"}
    qt=utc(item.get("signal_utc"))
    first=utc(item.get("first_quote_utc"))
    if qt is None or first is None or abs((qt-signal_t).total_seconds())>120 or first<qt:
        return {"status":"SIGNAL_TIME_MISMATCH"}
    if first<signal_t:
        return {"status":"QUOTE_BEFORE_HISTORICAL_SIGNAL"}
    entry={"status":item.get("entry_quote_status"),"first_quote_utc":first.isoformat(),
           "initial_sell_quote_raw":item.get("initial_sell_usdc_raw")}
    follows={}
    for horizon in (60,300,900):
        q=item.get("followups",{}).get(str(horizon),{})
        seen=utc(q.get("observed_utc"))
        if q.get("status")=="SELL_QUOTE_ONLY" and seen and 0<=(seen-(first+timedelta(seconds=horizon))).total_seconds()<=60 and str(q.get("sell_usdc_raw","")).isdigit():
            follows[str(horizon)]={"status":"INDICATIVE_SELL_QUOTE_VERIFIED",
                  "sell_usdc_raw":q["sell_usdc_raw"],"quote_observed_utc":seen.isoformat()}
        else:
            follows[str(horizon)]={"status":q.get("status","NOT_RECORDED"),"sell_usdc_raw":None}
    return {"status":"MATCHED_PROSPECTIVE_A_QUOTE_COHORT","entry":entry,
            "followups_from_first_quote":follows}

def analyze(snapshot_db, minute_db, cohort_path=None, hours=24, as_of=None):
    now=as_of or datetime.now(timezone.utc)
    if now.tzinfo is None or not 1<=hours<=744:raise ValueError("Invalid window/clock")
    begin=now-timedelta(hours=hours)
    signals=load_a_signals(snapshot_db,begin,now)
    minute=load_minute(minute_db,begin,now)
    cohort=None
    if cohort_path and Path(cohort_path).is_file():
        cohort=json.loads(Path(cohort_path).read_text())
        if cohort.get("version")!=1 or not isinstance(cohort.get("signals"),dict):
            raise ValueError("Bad prospective cohort")
    counts=Counter()
    out=[]
    for signal in signals:
        t=utc(signal["signal_utc"])
        series=minute.get((signal["mint"],signal["pair"]),[])
        baseline=next_sample(series,t,BASE_DELAY)
        item={**signal,"baseline":None,"horizons":{}}
        if baseline is None:
            counts["NO_POST_SIGNAL_BASELINE"]+=1
        else:
            bt,p0,l0,v0,b0,s0=baseline
            item["baseline"]={"observed_utc":bt.isoformat(),
                "delay_from_signal_seconds":round((bt-t).total_seconds(),2),
                "price_usd":p0,"liquidity_usd":l0,
                "volume_m5":v0,"buy_share":buy_share(b0,s0)}
            for h in HORIZONS:
                target=bt+timedelta(seconds=h)
                follow=next_sample(series,target,TOLERANCE) if target<=now else None
                if follow is None:
                    item["horizons"][str(h)]={"status":"NOT_DUE" if target>now else "MISSING"}
                    counts["MISSING_OR_NOT_DUE_"+str(h)]+=1
                    continue
                ft,p1,l1,v1,b1,s1=follow
                bs0=buy_share(b0,s0);bs1=buy_share(b1,s1)
                label,_=classify(p0,p1,l0,l1,False)
                item["horizons"][str(h)]={"status":"OBSERVED",
                    "observed_utc":ft.isoformat(),"price_change_pct":change_pct(p0,p1),
                    "liquidity_change_pct":change_pct(l0,l1),
                    "volume_change_pct":change_pct(v0,v1),
                    "buy_share_change_pp":round(100*(bs1-bs0),4)
                        if bs1 is not None and bs0 is not None else None,
                    "liquidity_label":label}
                counts["OBSERVED_"+str(h)]+=1
                if h==300:counts["FIVE_MIN_"+label]+=1
        item["quote_evidence"]=quote_join(cohort,signal["mint"],signal["pair"],t)
        counts[item["quote_evidence"]["status"]]+=1
        out.append(item)
    return {"mode":"HISTORICAL_A_PROXY_MINUTE_AND_PROSPECTIVE_QUOTE_JOIN_READ_ONLY",
        "checked_utc":now.isoformat(),"hours":hours,
        "a_proxy_signals_after_15m_pair_cooldown":len(signals),
        "unique_signal_pairs":len({(x["mint"],x["pair"]) for x in signals}),
        "counts":dict(counts),"records":out,
        "jupiter_requests":0,"source_db_writes":0,"orders_executed":0,
        "limitations":[
            "Historical A signals are proxies, not actual paper entries or independently executed trades.",
            "Cooldown is for deduplication, not a new trading filter.",
            "Market followups are relative to FIRST AVAILABLE POST-SIGNAL MINUTE OBSERVATION, not exact scan time.",
            "Market snapshots are DexScreener marks, not executable prices or swaps.",
            "Quotes only attach for exact matching prospectively recorded A cohorts and quote times.",
            "Quote horizons are relative to first_quote_utc and never equated with market horizon timestamps.",
            "Missing quotes remain missing; prior historical price rises are not backfilled with sell quotes.",
            "Overlapping signals and tokens limit statistical independence; no validated edge."]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshots",required=True)
    p.add_argument("--minute-db",required=True)
    p.add_argument("--cohort")
    p.add_argument("--hours",type=int,default=24)
    args=p.parse_args()
    r=analyze(args.snapshots,args.minute_db,args.cohort,args.hours)
    # Summary only by default: no huge console output
    print(json.dumps({k:v for k,v in r.items() if k!="records"},indent=2))
    print("EXAMPLE_RECORDS")
    print(json.dumps(r["records"][:5],indent=2))
if __name__=="__main__":main()
