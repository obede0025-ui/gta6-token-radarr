"""One-command manual V3.4 research cycle.

Reads production DB and paper portfolio read-only; only V3.3 ledger,
report, health JSON and HTML dashboard are written in dedicated folder.
Does not run any orders or modify active workers.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
from research_quote_cohort_v33 import run
from research_observer_health_v34 import metrics
from research_dashboard_v34 import render

def atomic_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=None
    try:
        with tempfile.NamedTemporaryFile("w",dir=path.parent,encoding="utf-8",
                                         prefix=path.name+".tmp-",delete=False) as f:
            tmp=f.name
            json.dump(obj,f,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp and os.path.exists(tmp):os.unlink(tmp)

def cycle(db,portfolio,output,clock=None,runner=run):
    db=Path(db).resolve();portfolio=Path(portfolio).resolve()
    folder=Path(output).resolve()
    ledger=folder/"research_ledger.json"
    report=folder/"research_report.json"
    health_path=folder/"research_health.json"
    dashboard=folder/"research_dashboard.html"
    protected={db,portfolio}
    if protected & {ledger,report,health_path,dashboard} or db==portfolio:
        raise ValueError("Research output may not replace sources")
    if not db.is_file() or not portfolio.is_file():
        raise FileNotFoundError("Both original sources required; no historical replay")
    outcome=runner(db,portfolio,ledger,report)
    state=json.loads(ledger.read_text(encoding="utf-8"))
    health=metrics(state,outcome,clock() if clock else None)
    atomic_json(health_path,health)
    html=render(state,health)
    temp=None
    try:
        with tempfile.NamedTemporaryFile("w",dir=folder,encoding="utf-8",
                  prefix="dashboard.tmp-",delete=False) as f:
            temp=f.name
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,dashboard)
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)
    return {"mode":"V34_MANUAL_RESEARCH_CYCLE","signals":health["signal_count"],
        "health_status":health["status"],"alerts":health["alerts"],
        "dashboard":str(dashboard),"paper_orders_executed":0,
        "live_orders_executed":0}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    p.add_argument("--portfolio",default="/home/ubuntu/gta6-paper-vps/data/v591/portfolio.json")
    p.add_argument("--output",default="data/v33")
    args=p.parse_args()
    print(json.dumps(cycle(args.db,args.portfolio,args.output),indent=2))

if __name__=="__main__":main()
