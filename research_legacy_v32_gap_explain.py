"""Explain legacy M1 -> V3.2 coverage without changing either state.

Read-only: missing M1 mints are separated by V3.2 bootstrap time,
known V3.2 skip status, and genuinely unexplained post-bootstrap gaps.
Do not call an omission a defect when it preceded V3.2 initialization.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("/home/ubuntu/gta6-v63-test")

def utc(value):
    if not isinstance(value,str):return None
    try:
        d=datetime.fromisoformat(value.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,OverflowError):return None

def explain(m1,v32):
    tracked=m1.get("tracked")
    positions=v32.get("positions")
    if not isinstance(tracked,dict) or not isinstance(positions,dict):
        raise ValueError("Expected M1 tracked and V3.2 positions dictionaries")
    skipped=v32.get("skipped",{})
    skipped=skipped if isinstance(skipped,dict) else {}
    boot=utc(v32.get("started_utc"))
    missing=sorted(set(tracked)-set(positions))
    details=[]
    classifications=Counter()
    for mint in missing:
        item=tracked[mint] if isinstance(tracked[mint],dict) else {}
        registered=utc(item.get("registered_utc"))
        reason=skipped.get(mint)
        if reason is not None:
            category="RECORDED_SKIP"
        elif boot and registered and registered<=boot:
            category="PRE_V32_BOOTSTRAP"
        elif boot and registered and registered>boot:
            category="UNEXPLAINED_POST_BOOTSTRAP"
        else:
            category="TIMESTAMP_OR_BOOTSTRAP_UNKNOWN"
        classifications[category]+=1
        details.append({"mint":mint,"registered_utc":item.get("registered_utc"),
                        "category":category,"skip_reason":reason})
    post=classifications["UNEXPLAINED_POST_BOOTSTRAP"]
    unknown=classifications["TIMESTAMP_OR_BOOTSTRAP_UNKNOWN"]
    status=("WARN" if post or unknown else
            "PASS" if not missing or classifications["PRE_V32_BOOTSTRAP"]+
            classifications["RECORDED_SKIP"]==len(missing) else "WARN")
    return {"status":status,"tracked_m1":len(tracked),
            "positions_v32":len(positions),"missing_total":len(missing),
            "v32_bootstrap_utc":v32.get("started_utc"),
            "classified":dict(classifications),"missing":details,
            "note":"Classification is based only on recorded timestamps and states; no missing quote is reconstructed."}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--m1",default=str(ROOT/"m1_v3_state.json"))
    p.add_argument("--v32",default=str(ROOT/"m1_v3_2_state.json"))
    a=p.parse_args()
    print(json.dumps(explain(json.loads(Path(a.m1).read_text()),
                             json.loads(Path(a.v32).read_text())),indent=2))

if __name__=="__main__":main()
