"""Prospective A/B research on the existing minute collection cycle.

One extra GET for proposed-only mints (maximum 15) per cycle; no new timer,
orders, original learning.db changes, B3 approvals or M1 signal changes.
Data are collected contemporaneously and stored in a separate SQLite file.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_minute_market_pilot import (
    API, MAX_TOKENS, ORIGINAL, PILOT, adaptive_candidate_preview, extract, fetch
)

ROOT=Path("/home/ubuntu/gta6-v32-gate-pilot")
COMPARISON=ROOT/"selection_shadow_comparison.sqlite"

def metrics(rows, found):
    known=active=base=missing=0
    for mint,pair,_ in rows:
        item=found.get(mint)
        if item is None or item.get("pair")!=pair:
            missing+=1
            continue
        liq=item.get("liquidity")
        vol=item.get("volume")
        buys=item.get("buys")
        sells=item.get("sells")
        if liq is not None:known+=1
        if vol is not None and vol>=250:active+=1
        if (liq is not None and liq>=20000 and vol is not None and vol>=250
            and buys is not None and sells is not None and buys>sells):
            base+=1
    return {"selected":len(rows),"known_liquidity":known,
            "volume_ge_250":active,"base_pass_without_age_or_momentum":base,
            "missing_exact_pairs":missing}

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS comparison_cycles (
        observed_at TEXT PRIMARY KEY,
        legacy_selected INTEGER, proposed_selected INTEGER,
        legacy_known INTEGER, proposed_known INTEGER,
        legacy_volume INTEGER, proposed_volume INTEGER,
        legacy_base INTEGER, proposed_base INTEGER,
        legacy_missing INTEGER, proposed_missing INTEGER,
        changed_pairs INTEGER, extra_requested INTEGER,
        error TEXT)""")

def write_cycle(path, stamp, legacy, proposed, changed, extra, error=None):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(target) as db:
        initialize(db)
        db.execute("""INSERT OR IGNORE INTO comparison_cycles VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            stamp,
            legacy["selected"] if legacy else None,
            proposed["selected"] if proposed else None,
            legacy["known_liquidity"] if legacy else None,
            proposed["known_liquidity"] if proposed else None,
            legacy["volume_ge_250"] if legacy else None,
            proposed["volume_ge_250"] if proposed else None,
            legacy["base_pass_without_age_or_momentum"] if legacy else None,
            proposed["base_pass_without_age_or_momentum"] if proposed else None,
            legacy["missing_exact_pairs"] if legacy else None,
            proposed["missing_exact_pairs"] if proposed else None,
            changed,extra, error[:300] if error else None))
    return {"logged_utc":stamp,"status":"ERROR" if error else "RECORDED",
            "changed_pairs":changed,"extra_requested":extra,
            "legacy":legacy,"proposed":proposed,
            "error":error}

def compare_cycle(source,pilot,session,now,legacy,legacy_found,path=COMPARISON):
    """Run after normal pilot collection succeeds. Never alters pilot data."""
    stamp=now.isoformat(timespec="seconds")
    try:
        with sqlite3.connect(Path(pilot).resolve().as_uri()+"?mode=ro",uri=True) as db:
            preview=adaptive_candidate_preview(source,db,now)
        proposed=preview["proposed"]
        if not proposed:
            return write_cycle(path,stamp,None,None,0,0,"EMPTY_CANDIDATE_POOL")
        lset={(m,p) for m,p,_ in legacy}
        added=[r for r in proposed if (r[0],r[1]) not in lset]
        # Exact-pair observations require a second request only for new mints;
        # if same mint has a different pair, re-request that mint.
        extra_found=extract(fetch(session,[x[0] for x in added]),added) if added else {}
        # A reused mint with a different pair must not borrow old-pair data.
        combined={m:item for m,item in legacy_found.items()
                  if (m,item.get("pair")) in lset}
        combined.update(extra_found)
        old=metrics(legacy,legacy_found)
        new=metrics(proposed,combined)
        return write_cycle(path,stamp,old,new,len(added),len(added))
    except Exception as exc:
        # Optional shadow failure must not interrupt primary 60s market writer.
        return write_cycle(path,stamp,None,None,0,0,
                           type(exc).__name__+": "+str(exc)[:220])

def report(path=COMPARISON,hours=24,now=None):
    now=now or datetime.now(timezone.utc)
    cutoff=(now-timedelta(hours=hours)).isoformat(timespec="seconds")
    # Before the first upgraded timer run, the comparison database does not
    # exist. Report an explicit waiting state instead of raising SQLite error.
    if not Path(path).is_file():
        return {"mode":"READ_ONLY_SELECTION_AB_COMPARISON",
                "window_hours":hours,"cycles_recorded":0,
                "cycles_successful":0,"cycles_failed":0,
                "first_cycle_utc":None,"last_cycle_utc":None,
                "decision":"WAITING_FOR_FIRST_CYCLE",
                "note":"No comparison SQLite yet. Confirm git pull and new selection_shadow field in minute market service journal."}
    with sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        rows=db.execute("""SELECT observed_at,legacy_known,proposed_known,
                          legacy_volume,proposed_volume,legacy_base,proposed_base,
                          legacy_missing,proposed_missing,changed_pairs,error
                          FROM comparison_cycles WHERE observed_at>=?
                          ORDER BY observed_at""",(cutoff,)).fetchall()
    clean=[r for r in rows if r[-1] is None]
    def summary(a,b):
        if not clean:return {"legacy_total":0,"proposed_total":0,"diff":0,
                              "cycles_proposed_better":0,"cycles_equal":0,
                              "cycles_proposed_worse":0}
        delta=[r[b]-r[a] for r in clean]
        return {"legacy_total":sum(r[a] for r in clean),
                "proposed_total":sum(r[b] for r in clean),
                "diff":sum(delta),
                "cycles_proposed_better":sum(x>0 for x in delta),
                "cycles_equal":sum(x==0 for x in delta),
                "cycles_proposed_worse":sum(x<0 for x in delta)}
    return {"mode":"READ_ONLY_SELECTION_AB_COMPARISON",
            "window_hours":hours,"cycles_recorded":len(rows),
            "cycles_successful":len(clean),"cycles_failed":len(rows)-len(clean),
            "first_cycle_utc":rows[0][0] if rows else None,
            "last_cycle_utc":rows[-1][0] if rows else None,
            "known_liquidity":summary(1,2),
            "volume_ge_250":summary(3,4),
            "base_filter_without_age_or_momentum":summary(5,6),
            "missing_exact_pairs":summary(7,8),
            "last_errors":[{"utc":r[0],"error":r[-1]} for r in rows if r[-1]][-5:],
            "decision":"NEEDS_MORE_PROSPECTIVE_DATA" if len(clean)<60 else
                        "DESCRIPTIVE_COMPARISON_ONLY",
            "limitations":[
                "The compared sets overlap; results are not independent trading outcomes.",
                "Base-pass count excludes pair age and momentum.",
                "GET snapshots are sequential, not perfectly simultaneous.",
                "No claims about fills, net profits or future performance.",
                "Comparison is recorded only while the existing market service runs."
            ]}

def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--report-only",action="store_true")
    p.add_argument("--path",default=str(COMPARISON))
    p.add_argument("--hours",type=int,default=24)
    a=p.parse_args()
    if not a.report_only:
        p.error("This module only supports --report-only; the minute pilot performs recording")
    print(json.dumps(report(a.path,a.hours),indent=2))

if __name__=="__main__":main()
