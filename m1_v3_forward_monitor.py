"""M1 V3: manual forward-only 15-minute Dexscreener study. No orders."""
import argparse,json,math,requests
from pathlib import Path
from datetime import datetime,timezone
from b3_candidate_audit import parsed
WINDOW=900
def positive(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>0 else None
    except (TypeError,ValueError,OverflowError):return None
def key(e):return [e["scanned_at"],e["mint"],e["pair"]]
def bootstrap(shadow,now):
    events=sorted(shadow["events"],key=key)
    return {"version":1,"cursor":key(events[-1]) if events else None,
            "tracked":{},"created_utc":now.isoformat()}
def market(session,mint,pair):
    r=session.get("https://api.dexscreener.com/latest/dex/tokens/"+mint,timeout=6)
    r.raise_for_status()
    for p in r.json().get("pairs") or []:
        if p.get("chainId")=="solana" and p.get("pairAddress")==pair and (p.get("baseToken") or {}).get("address")==mint:
            price=positive(p.get("priceUsd"))
            if price is None:raise ValueError("NO_VALID_PRICE")
            return {"price":price,"liquidity":positive((p.get("liquidity") or {}).get("usd"))}
    raise ValueError("EXACT_PAIR_MISSING")
def advance(state,shadow,now,fetch):
    events=sorted(shadow["events"],key=key)
    for e in events:
        k=key(e)
        if state["cursor"] is not None and k<=state["cursor"]:continue
        state["cursor"]=k
        if e.get("decision")!="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW":continue
        if e["mint"] in state["tracked"]:continue
        age=(now-parsed(e["scanned_at"])).total_seconds()
        live=sum(v["status"]=="WATCHING" for v in state["tracked"].values())
        status=("MISSED_WINDOW" if age<0 or age>=WINDOW else
                "CAPACITY_SKIPPED" if live>=3 else "WATCHING")
        state["tracked"][e["mint"]]={"mint":e["mint"],"pair":e["pair"],
            "signal":e["scanned_at"],"registered_utc":now.isoformat(),"b3_recorded_utc":e.get("recorded_utc"),"entry":positive(e.get("price_usd")),
            "status":status,"last_poll":None,"samples":[],"errors":[]}
    count=0
    for item in sorted(state["tracked"].values(),key=lambda v:v["signal"]):
        if item["status"]!="WATCHING":continue
        if (now-parsed(item["signal"])).total_seconds()>=WINDOW:
            item["status"]="WINDOW_ENDED";continue
        if item["last_poll"] and (now-parsed(item["last_poll"])).total_seconds()<60:continue
        if count>=3:break
        count+=1;item["last_poll"]=now.isoformat()
        try:
            quote=fetch(item["mint"],item["pair"])
            p=positive(quote.get("price"));entry=item["entry"]
            if p is None or entry is None:raise ValueError("BAD_PRICE")
            change=round((p/entry-1)*100,4)
            item["samples"].append({"at":now.isoformat(),"price":p,
                "liquidity":positive(quote.get("liquidity")),"change_pct":change})
            if change>=15 or change<=-10:
                item["status"]="OBSERVED_PRICE_TRIGGER"
                item["trigger"]="TP" if change>=15 else "SL"
        except (requests.RequestException,ValueError,TypeError) as exc:
            item["errors"].append({"at":now.isoformat(),"type":type(exc).__name__})
        item["samples"]=item["samples"][-20:]
        item["errors"]=item["errors"][-10:]
    return {"requests":count,"watching":sum(x["status"]=="WATCHING" for x in state["tracked"].values()),
            "tracked":len(state["tracked"])}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--shadow",required=True);p.add_argument("--state",required=True)
    a=p.parse_args()
    now=datetime.now(timezone.utc)
    shadow=json.loads(Path(a.shadow).read_text())
    if shadow.get("version")!=1 or not isinstance(shadow.get("events"),list):raise ValueError("Invalid B3 state")
    path=Path(a.state)
    if not path.exists():
        state=bootstrap(shadow,now)
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("x") as f:json.dump(state,f,indent=2)
        print(json.dumps({"bootstrapped":True,"note":"Historical events skipped"}));return
    state=json.loads(path.read_text())
    if state.get("version")!=1 or not isinstance(state.get("tracked"),dict):raise ValueError("Invalid M1 state")
    with requests.Session() as s:result=advance(state,shadow,now,lambda m,p:market(s,m,p))
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(state,indent=2)+"\n");tmp.replace(path)
    print(json.dumps(result))
if __name__=="__main__":main()
