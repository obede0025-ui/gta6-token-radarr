"""Read-only token discovery funnel and missed-opportunity inventory.

Distinguishes unique tokens from repeated snapshots. Counts are coverage
metrics, not evidence of profitable trades. No API calls and no state writes.
"""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

APPROVED="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW"


def date(value):
    try:
        out=datetime.fromisoformat(value.replace("Z","+00:00"))
        return out.astimezone(timezone.utc) if out.tzinfo else None
    except (AttributeError,TypeError,ValueError):
        return None


def funnel(b3,m1,v32,db_conn=None):
    events=b3.get("events")
    monitored=m1.get("tracked")
    positions=v32.get("positions")
    if not isinstance(events,list) or not isinstance(monitored,dict) or not isinstance(positions,dict):
        raise ValueError("Unsupported state structure")
    started=date(m1.get("created_utc"))
    if started is None:
        raise ValueError("Missing M1 bootstrap date")
    all_tokens=set()
    b3_approved=set()
    relevant_approved=set()
    rejection=Counter()
    for e in events:
        mint=e.get("mint")
        if not isinstance(mint,str) or not mint:
            continue
        all_tokens.add(mint)
        if e.get("decision")==APPROVED:
            b3_approved.add(mint)
            stamp=date(e.get("recorded_utc")) or date(e.get("scanned_at"))
            # Use first observed recorded time where available. Old records
            # are explicitly not comparable to post-bootstrap cohorts.
            if stamp and stamp>=started and e.get("recorded_utc"):
                relevant_approved.add(mint)
        else:
            for reason in set(e.get("reasons") or []):
                rejection[reason]+=1
    statuses=Counter(x.get("status","UNKNOWN") for x in monitored.values())
    qstatuses=Counter(x.get("status","UNKNOWN") for x in positions.values())
    all_db_unique=None
    if db_conn is not None:
        all_db_unique=db_conn.execute("SELECT COUNT(DISTINCT token_address) FROM snapshots WHERE token_address IS NOT NULL").fetchone()[0]
    return {
        "mode":"DISCOVERY_FUNNEL_READ_ONLY",
        "summary":{
            "db_unique_tokens_all_time":all_db_unique,
            "b3_unique_seen_in_retained_events":len(all_tokens),
            "b3_approved_unique":len(b3_approved),
            "b3_new_approved_since_m1_with_precise_timestamp":len(relevant_approved),
            "m1_tracked_unique":len(monitored),
            "m1_new_cohort_approved_untracked":len(relevant_approved-set(monitored)),
            "m1_statuses":dict(statuses),
            "v32_recorded_unique":len(positions),
            "v32_statuses":dict(qstatuses),
            "v32_quoted_entries":sum(p.get("entry_token_raw") is not None for p in positions.values()),
            "v32_with_exit_quote":sum(any(q.get("status")=="QUOTE_AVAILABLE" for q in p.get("exit_quotes",[])) for p in positions.values()),
        },
        "b3_rejection_reasons_event_counts":dict(rejection.most_common()),
        "coverage_gaps":{
            "post_timestamp_b3_unique_approved_but_not_m1":[m[:12] for m in sorted(relevant_approved-set(monitored))][:30],
            "m1_capacity_skipped":[m[:12] for m,p in monitored.items() if p.get("status")=="CAPACITY_SKIPPED"][:30],
            "m1_missed_window":[m[:12] for m,p in monitored.items() if p.get("status")=="MISSED_WINDOW"][:30],
            "m1_not_recorded_in_v32":[m[:12] for m in monitored if m not in positions][:30],
        },
        "limitations":[
            "B3 state has a rolling events cap; these are not lifetime totals.",
            "DB unique token count is cumulative and may include unrelated rejected pairs.",
            "B3 rejection counts are events, not unique tokens, and reasons may overlap.",
            "Precision-based B3 cohort counts only events with recorded_utc after M1 creation.",
            "M1 and V3.2 deliberately ignore pre-bootstrap records.",
            "More candidates may increase opportunity and API cost; it does not establish positive expected return.",
        ],
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--b3",required=True)
    parser.add_argument("--m1",required=True)
    parser.add_argument("--v32",required=True)
    parser.add_argument("--db")
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    b3,m1,v32=(json.loads(Path(path).read_text(encoding="utf-8"))
               for path in (args.b3,args.m1,args.v32))
    if args.db:
        db=Path(args.db).resolve()
        if not db.is_file():raise FileNotFoundError(db)
        with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as conn:
            report=funnel(b3,m1,v32,conn)
    else:
        report=funnel(b3,m1,v32)
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".tmp")
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(target)
    print(json.dumps(report["summary"],ensure_ascii=False,indent=2))
    print("TOP_B3_REJECTION_EVENT_REASONS",json.dumps(list(report["b3_rejection_reasons_event_counts"].items())[:6]))


if __name__=="__main__":
    main()
