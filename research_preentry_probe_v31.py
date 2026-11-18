"""V3.1 bounded prospective pre-entry evidence probe; NEVER TRADES.

Reads existing SQLite DB and portfolio read-only, selects newest candidates
with original momentum filters. Uses Jupiter keyless quote-only GET and
Solana read-only getAccountInfo RPC, then pure strict evidence gate. No
wallet, orders, transaction build/sign, DB or portfolio mutations.
"""
import argparse
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
import requests

from radar_pretrade_safety import select_candidates
from research_discovery_quote_v27 import USDC,BUY_USDC_RAW,ORDER_URL
from radar_jupiter_pacing_v30 import SHARED_PACER
from radar_token_security import evaluate_mint,rpc_get_account,DEFAULT_RPC
from research_preentry_evidence_v31 import evaluate

MAX_CANDIDATES=5

class KeylessQuoteOnly:
    """Keyless Jupiter GET-only quote client with V3.0 bounded retries."""
    def __init__(self):
        self.session=requests.Session()

    def quote(self,input_mint,output_mint,amount):
        if not isinstance(amount,int) or amount<=0:
            raise ValueError("Invalid quote amount")
        response=SHARED_PACER.get(self.session,ORDER_URL,
            params={"inputMint":input_mint,"outputMint":output_mint,
                    "amount":str(amount)},timeout=15)
        payload=response.json()
        if not isinstance(payload,dict) or payload.get("error") or payload.get("errorCode"):
            raise ValueError("Jupiter route error")
        if payload.get("inputMint",input_mint)!=input_mint or payload.get("outputMint",output_mint)!=output_mint:
            raise ValueError("Jupiter mint mismatch")
        out=payload.get("outAmount")
        if not isinstance(out,str) or not out.isdigit() or int(out)<=0:
            raise ValueError("No positive quote")
        return int(out)

    def close(self):
        self.session.close()

def selection(db_path,state_path,now,limit=MAX_CANDIDATES,ignore_paper_capacity=False):
    if not 1<=limit<=MAX_CANDIDATES:raise ValueError("bounded maximum 5")
    if not db_path.is_file() or not state_path.is_file():
        raise FileNotFoundError("Existing source DB or paper state missing")
    state=json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("version")!=1 or not all(isinstance(state.get(k),dict) for k in ("traded","open","closed")):
        raise ValueError("Invalid paper portfolio")
    with sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise RuntimeError("DB failed quick_check")
        # Shadow evidence mode: ignore only OPEN POSITION CAPACITY in memory.
        # Historic traded lists and all signal/market thresholds remain intact.
        research_state=state
        if ignore_paper_capacity:
            research_state=dict(state)
            research_state["open"]={k:{} for k in ("A","B","C")}
        selected=select_candidates(db,research_state,now,limit)
        for c in selected:
            row=db.execute("""SELECT buys_m5,sells_m5,pair_age_hours
              FROM snapshots WHERE token_address=? AND pair_address=?
              AND scanned_at=? LIMIT 1""",(c["mint"],c["pair"],c["scanned_at_utc"])).fetchone()
            c["buys_m5"]=row[0] if row else None
            c["sells_m5"]=row[1] if row else None
            c["pair_age_hours"]=row[2] if row else None
    return selected

def probe_candidate(c,quoter,rpc_session,rpc_url,clock):
    # Always create independent route and authority evidence. A token
    # discovered in one pool can be routed through another by Jupiter.
    mint=c["mint"];stamp=clock().isoformat(timespec="seconds")
    route={"mint":mint,"discovery_pair":c["pair"],"checked_utc":stamp,
           "buy_quote_only":False,"sell_quote_only":False,
           "buy_status":"UNAVAILABLE","sell_status":"UNAVAILABLE",
           "stake_usdc":"10","immediate_sell_usdc":None}
    try:
        buy=quoter.quote(USDC,mint,BUY_USDC_RAW)
        route["buy_quote_only"]=True;route["buy_status"]="OK"
        try:
            sell=quoter.quote(mint,USDC,buy)
            route["sell_quote_only"]=True;route["sell_status"]="OK"
            route["immediate_sell_usdc"]=str(sell/1_000_000)
        except (requests.RequestException,ValueError,TypeError):
            route["sell_status"]="UNAVAILABLE"
    except (requests.RequestException,ValueError,TypeError):
        route["buy_status"]="UNAVAILABLE"
    try:
        account=rpc_get_account(rpc_session,rpc_url,mint)
        security=evaluate_mint(mint,["new_preentry_candidate"],account)
    except (requests.RequestException,ValueError,TypeError,KeyError):
        security=evaluate_mint(mint,["new_preentry_candidate"],None)
    security["checked_utc"]=clock().isoformat(timespec="seconds")
    return evaluate(c,route,security,clock()),route,security

def run(db_path,state_path,limit=MAX_CANDIDATES,rpc_url=DEFAULT_RPC,clock=None,ignore_paper_capacity=False):
    clock=clock or (lambda:datetime.now(timezone.utc))
    now=clock()
    if not rpc_url.startswith("https://"):
        raise ValueError("HTTPS RPC only")
    selected=selection(Path(db_path),Path(state_path),now,limit,ignore_paper_capacity)
    results=[]
    with requests.Session() as rpc:
        q=KeylessQuoteOnly()
        try:
            for c in selected:
                verdict,route,security=probe_candidate(c,q,rpc,rpc_url,clock)
                results.append({
                    "candidate":{"mint":c["mint"],"pair":c["pair"],"strategy":c["strategy"],
                      "scanned_at_utc":c["scanned_at_utc"]},
                    "evidence_state":verdict["evidence_state"],
                    "reasons":verdict["reasons"],
                    "quote_status":{"buy":route["buy_status"],"sell":route["sell_status"]},
                    "token_security_gate":security["security_gate"],
                    "paper_entry_permission":"BLOCK",
                    "live_trade_permission":"BLOCK"})
        finally:q.close()
    return {"mode":"V31_PROSPECTIVE_EVIDENCE_PAPER_ONLY",
       "paper_capacity_ignored_for_research_only":bool(ignore_paper_capacity),
       "checked_utc":clock().isoformat(timespec="seconds"),
       "candidate_count":len(selected),"research_evidence_passed":
           sum(r["evidence_state"]=="PASS_RESEARCH_ONLY" for r in results),
       "results":results,"trades_executed":0,"portfolio_writes":0,"database_writes":0,
       "note":"Cannot prove fills, holder concentration or safe selling; all trade approvals blocked."}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="data/learning.db")
    p.add_argument("--state",default="data/v591/portfolio.json")
    p.add_argument("--limit",type=int,default=MAX_CANDIDATES)
    p.add_argument("--shadow-capacity",action="store_true",
      help="Ignore existing paper-position capacity IN MEMORY for research only")
    p.add_argument("--rpc-url",default=DEFAULT_RPC)
    args=p.parse_args()
    print(json.dumps(run(args.db,args.state,args.limit,args.rpc_url,
      ignore_paper_capacity=args.shadow_capacity),indent=2))
