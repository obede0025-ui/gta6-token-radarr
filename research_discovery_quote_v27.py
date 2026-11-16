"""V2.7 first-signal Jupiter route-quote paper audit, never transact.

Prospective 25-cycle trial, frozen first qualifying signal for each EXACT
mint/pair across both V2.2 cohorts, keyless Jupiter Swap V2 quote-only GET.
Two separate DB files, no touching V2.6 or production. No wallet, no POST.
Quote availability is not an executable fill guarantee.
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime,timezone
from pathlib import Path
import requests

from research_discovery_outcomes_v26 import HORIZONS,MAX_LATENESS
from research_discovery_quality_v22 import execute

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
SOURCE=ROOT/"discovery_v27_source.sqlite"
OUTCOMES=ROOT/"discovery_v27_quotes.sqlite"
USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
ORDER_URL="https://api.jup.ag/swap/v2/order"
BUY_USDC_RAW=10_000_000 # $10, USDC has 6 decimals
RATE_SEC=2.2
CYCLES=25
DISCOVERY_CYCLES=10
MAX_ERRORS=3

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS first_signals(
       mint TEXT NOT NULL,pair TEXT NOT NULL,signal_utc TEXT NOT NULL,
       cohorts TEXT NOT NULL,entry_price_usd REAL,entry_liquidity_usd REAL,
       buy_raw_out TEXT,entry_sell_usdc_raw TEXT,buy_status TEXT NOT NULL,
       PRIMARY KEY(mint,pair))""")
    db.execute("""CREATE TABLE IF NOT EXISTS quote_followups(
       mint TEXT NOT NULL,pair TEXT NOT NULL,horizon_sec INTEGER NOT NULL,
       observed_utc TEXT NOT NULL,elapsed_sec REAL NOT NULL,
       quote_usdc_raw TEXT,status TEXT NOT NULL,
       PRIMARY KEY(mint,pair,horizon_sec))""")

class Quoter:
    """Rate-limited quote-only Jupiter client; no other HTTP methods."""
    def __init__(self,session=None,clock=time.monotonic,sleep=time.sleep):
        self.session=session or requests.Session()
        self.owns_session=session is None
        self.clock=clock;self.sleep=sleep;self.last_call=None

    def close(self):
        if self.owns_session:self.session.close()

    def quote(self,input_mint,output_mint,amount):
        if not isinstance(amount,int) or amount<=0:
            raise ValueError("raw quote amount must be positive integer")
        if self.last_call is not None:
            wait=RATE_SEC-(self.clock()-self.last_call)
            if wait>0:self.sleep(wait)
        self.last_call=self.clock()
        response=self.session.get(ORDER_URL,
             params={"inputMint":input_mint,"outputMint":output_mint,
                     "amount":str(amount)},timeout=20)
        response.raise_for_status()
        data=response.json()
        if not isinstance(data,dict):
            raise ValueError("Malformed Jupiter order response")
        if data.get("error") or data.get("errorCode"):
            raise ValueError("Jupiter order error: "+str(data.get("error") or data.get("errorCode"))[:100])
        if data.get("inputMint",input_mint)!=input_mint or data.get("outputMint",output_mint)!=output_mint:
            raise ValueError("Quote token mismatch")
        out=data.get("outAmount")
        if not isinstance(out,str) or not out.isdigit() or int(out)<=0:
            raise ValueError("No positive Jupiter quote outAmount")
        return int(out)

def collect_new_signals(source,db,now,quoter):
    stamp=now.isoformat(timespec="seconds")
    with sqlite3.connect(Path(source).resolve().as_uri()+"?mode=ro",uri=True) as original:
        original.execute("PRAGMA query_only=ON")
        rows=original.execute("""SELECT mint,pair,cohort,price_usd,liquidity_usd
             FROM quality_cohort_samples WHERE observed_at=? AND momentum_pass=1
             ORDER BY mint,pair,cohort""",(stamp,)).fetchall()
    grouped={}
    for mint,pair,cohort,price,liq in rows:
        grouped.setdefault((mint,pair),{"cohorts":set(),"price":price,"liq":liq})["cohorts"].add(cohort)
    added=0
    for (mint,pair),details in grouped.items():
        if db.execute("SELECT 1 FROM first_signals WHERE mint=? AND pair=?",(mint,pair)).fetchone():
            continue
        status="NO_BUY_QUOTE";buy=None;initial_sell=None
        try:
            buy=quoter.quote(USDC,mint,BUY_USDC_RAW)
            try:
                initial_sell=quoter.quote(mint,USDC,buy)
                status="BUY_AND_INITIAL_SELL_QUOTED"
            except Exception:
                status="BUY_QUOTED_SELL_UNAVAILABLE"
        except Exception:
            status="NO_BUY_QUOTE"
        db.execute("""INSERT INTO first_signals VALUES(?,?,?,?,?,?,?,?,?)""",
           (mint,pair,stamp,"+".join(sorted(details["cohorts"])),
            details["price"],details["liq"],str(buy) if buy else None,
            str(initial_sell) if initial_sell else None,status))
        db.commit()
        added+=1
    return added

def due_quotes(db,now):
    due=[];missed=[]
    for mint,pair,stamp,buy in db.execute(
       "SELECT mint,pair,signal_utc,buy_raw_out FROM first_signals").fetchall():
        elapsed=(now-datetime.fromisoformat(stamp)).total_seconds()
        for h in HORIZONS:
            if elapsed<h:continue
            if db.execute("""SELECT 1 FROM quote_followups
                WHERE mint=? AND pair=? AND horizon_sec=?""",(mint,pair,h)).fetchone():continue
            item=(mint,pair,h,elapsed,buy)
            (missed if elapsed>h+MAX_LATENESS else due).append(item)
    return due,missed

def followup(db,now,quoter,now_fn=lambda:datetime.now(timezone.utc)):
    due,missed=due_quotes(db,now)
    # Quote requests can take time. A horizon is not retroactively recovered
    # if its request actually begins after the allowed window.
    for mint,pair,h,elapsed,buy in due+missed:
        start=now if (mint,pair,h,elapsed,buy) in missed else now_fn()
        elapsed_actual=(start-datetime.fromisoformat(db.execute(
            "SELECT signal_utc FROM first_signals WHERE mint=? AND pair=?",(mint,pair)).fetchone()[0])).total_seconds()
        if elapsed>h+MAX_LATENESS or elapsed_actual>h+MAX_LATENESS:
            status="HORIZON_MISSED";value=None
        elif buy is None:
            status="NO_ENTRY_BUY_QUOTE";value=None
        else:
            try:
                value=quoter.quote(mint,USDC,int(buy))
                status="JUPITER_SELL_ROUTE_QUOTED"
            except Exception:
                value=None;status="SELL_ROUTE_UNAVAILABLE"
        db.execute("""INSERT OR IGNORE INTO quote_followups VALUES(?,?,?,?,?,?)""",
             (mint,pair,h,start.isoformat(timespec="seconds"),elapsed_actual,
              str(value) if value else None,status))
        db.commit()
    return len(due),len(missed)

def report(path=OUTCOMES,errors=None,completed=None):
    result={"mode":"DISCOVERY_V27_FIRST_SIGNAL_QUOTE_PAPER",
            "status":"NO_DATABASE","errors":errors or [],
            "cycles_completed":completed,
            "quote_not_fill":True,
            "amount_usdc":10,
            "horizons_seconds":list(HORIZONS)}
    if not Path(path).is_file():return result
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        entries=db.execute("""SELECT mint,pair,signal_utc,cohorts,
               buy_raw_out,entry_sell_usdc_raw,buy_status FROM first_signals
               ORDER BY signal_utc,mint""").fetchall()
        values=[]
        for mint,pair,stamp,cohorts,buy,initial,status in entries:
            rows=db.execute("""SELECT horizon_sec,status,quote_usdc_raw,elapsed_sec
                FROM quote_followups WHERE mint=? AND pair=?""",(mint,pair)).fetchall()
            measures={}
            for h,s,q,elapsed in rows:
                # USD output is USDC raw / 1e6, 10 USDC entry; includes
                # Jupiter quoted route costs, excludes gas and fill risk.
                pct=round((int(q)/BUY_USDC_RAW-1)*100,3) if q is not None else None
                measures[str(h)]={"status":s,"elapsed_seconds":round(elapsed,1),
                   "usdc_sell_quote":round(int(q)/1e6,6) if q else None,
                   "paper_quote_return_pct_before_network_fees":pct}
            values.append({"mint":mint,"pair":pair,"signal_utc":stamp,
                "cohorts":cohorts,"buy_status":status,
                "entry_buy_token_raw":buy,
                "immediate_reversal_usdc":round(int(initial)/1e6,6) if initial else None,
                "outcomes":measures})
        result["unique_first_signal_pairs"]=len(entries)
        result["signals"]=values
        result["status"]="OK" if entries else "NO_NEW_SIGNALS"
    result["safety"]={"api_get_only":True,"orders_executed":0,
          "wallet_required":False,"source_db_unmodified":True}
    result["limitations"]=[
        "First signal per exact mint/pair across both cohorts, not repeated signals.",
        "Jupiter routes may use pools other than the original discovery pair.",
        "Buy amount is hypothetical: no executed acquisition or guaranteed tokens.",
        "Repeated sell quote uses the hypothetical buy outAmount; no fill is verified.",
        "Network fees, token transfer taxes, price changes during requests and execution failure are excluded.",
        "No claims of realizable profits from quote-only routes."]
    return result

def run(source=SOURCE,outcomes=OUTCOMES,cycles=CYCLES,discovery_cycles=DISCOVERY_CYCLES,
        sample_fn=execute,clock=time.monotonic,sleep=time.sleep,
        now_fn=lambda:datetime.now(timezone.utc),quoter=None):
    if cycles!=25 or discovery_cycles!=10:
        raise ValueError("fixed protocol: 25 cycles, first 10 discovery")
    source=Path(source);outcomes=Path(outcomes)
    if source.resolve()==outcomes.resolve():raise ValueError("separate databases required")
    if source.exists() or outcomes.exists():
        raise FileExistsError("V2.7 databases already exist. Use --report-only or new --source and --outcomes paths. Never delete old cohorts.")
    outcomes.parent.mkdir(parents=True,exist_ok=True)
    collector=quoter or Quoter()
    errors=[];consecutive=0;completed=0;anchor=clock()
    try:
        with sqlite3.connect(outcomes) as db:
            initialize(db)
            for i in range(cycles):
                wait=anchor+i*60-clock()
                if wait>0:sleep(wait)
                try:
                    now=now_fn()
                    if i<discovery_cycles:
                        result=sample_fn(shadow=source,now=now,slots=20)
                        if result.get("status")!="RECORDED":
                            raise RuntimeError("Bad source sample status "+str(result.get("status")))
                        collect_new_signals(source,db,now,collector)
                    followup(db,now_fn(),collector,now_fn=now_fn)
                    consecutive=0
                except Exception as exc:
                    errors.append({"cycle":i+1,"type":type(exc).__name__,"error":str(exc)[:200]})
                    consecutive+=1
                    if consecutive>=MAX_ERRORS:break
                completed+=1
                lag=clock()-(anchor+(i+1)*60)
                if lag>0:anchor=clock()-i*60
    finally:
        if quoter is None:collector.close()
    return report(outcomes,errors,completed)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",default=str(SOURCE))
    p.add_argument("--outcomes",default=str(OUTCOMES))
    p.add_argument("--report-only",action="store_true")
    args=p.parse_args()
    result=report(args.outcomes) if args.report_only else run(args.source,args.outcomes)
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
