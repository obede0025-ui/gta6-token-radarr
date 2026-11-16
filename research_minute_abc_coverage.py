"""One-shot A/B/C market coverage experiment, never a trading gate.

A = current 30-token source selection, B = existing shadow v2 rotation,
C = independent GeckoTerminal indexed pool reserve coverage on selected
pinned Solana pool IDs (not a direct Solana RPC balance/route check).
No database or state writes. API calls bounded. Run manually only.
"""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from research_minute_market_pilot import (
    ORIGINAL, PILOT, MAX_TOKENS, API, adaptive_candidate_preview,
    extract, finite
)

GECKO_MULTI="https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/"
GECKO_HEADERS={"Accept":"application/json;version=20230203"}

def fetch_dex(session, rows):
    """Batch mints to keep DexScreener token GET bounded at <=30 mints."""
    mints=list(dict.fromkeys(m for m,_,_ in rows))
    payload=[]
    for start in range(0,len(mints),30):
        response=session.get(API+",".join(mints[start:start+30]),timeout=20)
        response.raise_for_status()
        chunk=response.json()
        if not isinstance(chunk,list):
            raise ValueError("DexScreener unexpected response")
        payload.extend(chunk)
    return payload

def cohort_metrics(rows, payload):
    found=extract(payload,rows)
    missing=unknown=known=base_pass=0
    statuses=Counter()
    for mint,pair,_ in rows:
        data=found.get(mint)
        if not data or data.get("pair")!=pair or data.get("price") is None or data["price"]<=0:
            missing+=1
            statuses["MARKET_DATA_UNAVAILABLE"]+=1
            continue
        liq=data.get("liquidity")
        if liq is None:
            unknown+=1
            statuses["LIQUIDITY_UNKNOWN"]+=1
            continue
        known+=1
        statuses["QUOTE_MARKET_OBSERVED"]+=1
        volume=data.get("volume")
        buys=data.get("buys")
        sells=data.get("sells")
        if liq>=20000 and volume is not None and volume>=250 and buys is not None and sells is not None and buys>sells:
            base_pass+=1
    return {"selected":len(rows),"unique_mints":len({r[0] for r in rows}),
            "known_liquidity":known,"unknown_liquidity":unknown,
            "market_unavailable":missing,
            "base_pass_without_age_or_momentum":base_pass,
            "statuses":dict(statuses)}

def parse_gecko(payload, requested):
    """Require exact requested network+pool ID; missing data is UNKNOWN."""
    results={}
    for item in payload.get("data",[]) if isinstance(payload,dict) else []:
        if not isinstance(item,dict):
            continue
        ident=item.get("id","")
        attrs=item.get("attributes") or {}
        pool_addr=attrs.get("address")
        if not pool_addr and isinstance(ident,str) and ident.startswith("solana_"):
            pool_addr=ident[len("solana_"):]
        if pool_addr not in requested or ident not in ("solana_"+pool_addr,pool_addr):
            continue
        reserve=finite(attrs.get("reserve_in_usd"))
        if reserve is not None and reserve>=0:
            results[pool_addr]=reserve
    return results

def gecko_check(session, rows, limit=10):
    """One bounded independent indexer GET for up to 10 distinct pinned pools."""
    unique=list(dict.fromkeys(pair for _,pair,_ in rows))[:limit]
    if not unique:
        return {"checked_pools":0,"available":0,"missing":0,"results":[]}
    response=session.get(GECKO_MULTI+",".join(unique),headers=GECKO_HEADERS,timeout=20)
    response.raise_for_status()
    data=response.json()
    reserves=parse_gecko(data,set(unique))
    results=[{"pair":pool,"geckoterminal_reserve_usd":reserves.get(pool),
              "data_available":pool in reserves} for pool in unique]
    return {"checked_pools":len(unique),"available":len(reserves),
            "missing":len(unique)-len(reserves),"results":results}

def experiment(original=ORIGINAL,pilot=PILOT,now=None,session=None,c_limit=10):
    now=now or datetime.now(timezone.utc)
    if not 1<=c_limit<=10:
        raise ValueError("C limit must be 1..10")
    with sqlite3.connect(Path(original).resolve().as_uri()+"?mode=ro",uri=True) as original_db, \
         sqlite3.connect(Path(pilot).resolve().as_uri()+"?mode=ro",uri=True) as history_db:
        original_db.execute("PRAGMA query_only=ON")
        history_db.execute("PRAGMA query_only=ON")
        preview=adaptive_candidate_preview(original_db,history_db,now,limit=MAX_TOKENS)
    a,b=preview["legacy"],preview["proposed"]
    all_rows=list({(m,p):(m,p,s) for m,p,s in a+b}.values())
    def run(client):
        payload=fetch_dex(client,all_rows)
        am,bm=cohort_metrics(a,payload),cohort_metrics(b,payload)
        # C observes only currently unknown pinned pools, without substitution.
        a_found=extract(payload,a)
        b_found=extract(payload,b)
        unknown=[]
        for mint,pair,stamp in a+b:
            item=(a_found if (mint,pair,stamp) in a else b_found).get(mint)
            if item and item.get("pair")==pair and item.get("price") is not None and item["price"]>0 and item.get("liquidity") is None:
                unknown.append((mint,pair,stamp))
        c_error=None
        try:
            cm=gecko_check(client,unknown,limit=c_limit)
        except (requests.RequestException,ValueError,TypeError) as exc:
            c_error=type(exc).__name__+": "+str(exc)[:180]
            cm={"checked_pools":0,"available":0,"missing":0,"results":[]}
        return {"mode":"ONE_SHOT_AB_C_SHADOW_COVERAGE",
                "observed_utc":now.isoformat(),
                "A_current_selection":am,"B_shadow_v2_selection":bm,
                "B_minus_A":{"known_liquidity":bm["known_liquidity"]-am["known_liquidity"],
                             "base_pass_without_age_or_momentum":bm["base_pass_without_age_or_momentum"]-am["base_pass_without_age_or_momentum"]},
                "changed_slots":preview["diagnostics"].get("changed_slots"),
                "C_independent_pool_indexer":{"source":"GeckoTerminal public Solana pool indexer",
                    "note":"Indexed on-chain-derived reserve_in_usd, not direct RPC verified balances.",
                    **cm,"error":c_error},
                "api_calls":{"dexscreener":(len({x[0] for x in all_rows})+29)//30,
                             "geckoterminal":int(bool(unknown))},
                "no_database_writes":True,"no_real_trades":True,
                "limitations":[
                    "A/B are overlapping cohorts observed in a shared retrieval window.",
                    "GET calls are sequential; comparison not perfectly simultaneous.",
                    "A/B base pass excludes token age, momentum, and quote execution.",
                    "C indexer coverage does not verify token identity, pool safety or executable liquidity.",
                    "Alternative pool values never replace missing pinned liquidity.",
                    "This single snapshot does not demonstrate better detection or profitability."]}
    if session is not None:
        return run(session)
    with requests.Session() as client:
        return run(client)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original",default=ORIGINAL)
    p.add_argument("--pilot",default=PILOT)
    p.add_argument("--c-limit",type=int,default=10)
    args=p.parse_args()
    print(json.dumps(experiment(args.original,args.pilot,c_limit=args.c_limit),indent=2))

if __name__=="__main__":
    main()
