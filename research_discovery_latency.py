"""Read-only discovery-to-quote latency audit; no network calls or live changes.

Cohort: V3.2 token mints. DB MIN(scanned_at) means FIRST RETAINED SNAPSHOT,
not token launch time. State timestamps indicate events recorded by this system.
All negative and missing deltas are explicitly reported, not corrected.
"""
import argparse
import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def stamp(value):
    if value is None or not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except ValueError:
        return None


def delta(later, earlier):
    if not later or not earlier:
        return None
    return round((later-earlier).total_seconds(), 3)


def stats(rows):
    nums = sorted(v for v in rows if v is not None and v >= 0)
    if not nums:
        return {"n": 0, "median_s": None, "p90_s": None, "max_s": None}
    return {
        "n": len(nums), "median_s": round(statistics.median(nums), 2),
        "p90_s": nums[min(len(nums)-1, int((len(nums)-1)*.9))],
        "max_s": nums[-1],
    }


def first_event(events, mint, approved_only=False):
    candidates=[]
    for event in events:
        if not isinstance(event, dict) or event.get("mint") != mint:
            continue
        if approved_only and event.get("decision") != "REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW":
            continue
        t = stamp(event.get("recorded_utc")) or stamp(event.get("scanned_at"))
        if t:
            candidates.append(t)
    return min(candidates) if candidates else None


def analyze(b3, m1, v32, db):
    positions = v32.get("positions")
    if not isinstance(positions, dict):
        raise ValueError("V3.2 positions must be a dict")
    events = b3.get("events") or []
    tracked = m1.get("tracked") or {}
    if not isinstance(tracked, dict):
        tracked={}
    result=[]
    for mint, pos in sorted(positions.items()):
        if not isinstance(pos,dict):
            continue
        first = db.execute(
            "SELECT MIN(scanned_at), COUNT(*) FROM snapshots WHERE token_address=?",
            (mint,)).fetchone()
        first_db = stamp(first[0]) if first else None
        b3_seen = first_event(events, mint)
        b3_approved = first_event(events, mint, approved_only=True)
        signal = stamp(pos.get("signal_utc"))
        quoted = stamp(pos.get("entry_quote_utc"))
        entry_lag = delta(quoted, signal)
        tracked_item = tracked.get(mint)
        # Preserve track field evidence without assuming its schema.
        known_track = {}
        if isinstance(tracked_item, dict):
            for key, val in tracked_item.items():
                if any(term in key.lower() for term in ("utc","time","first","created")):
                    parsed=stamp(val)
                    if parsed:
                        known_track[key]=parsed.isoformat()
        result.append({
            "mint": mint, "snapshots": first[1] if first else 0,
            "first_retained_snapshot_utc": first_db.isoformat() if first_db else None,
            "first_retained_b3_event_utc": b3_seen.isoformat() if b3_seen else None,
            "first_retained_b3_approval_utc": b3_approved.isoformat() if b3_approved else None,
            "v32_signal_utc": signal.isoformat() if signal else None,
            "v32_quote_utc": quoted.isoformat() if quoted else None,
            "m1_tracked": isinstance(tracked_item,dict),
            "m1_timestamp_fields": known_track,
            "first_snapshot_to_signal_s": delta(signal, first_db),
            "first_b3_approval_to_signal_s": delta(signal, b3_approved),
            "signal_to_quote_s": entry_lag,
            "first_snapshot_to_quote_s": delta(quoted, first_db),
            "entry_lag_field_s": pos.get("entry_lag_seconds"),
        })
    stages = ("first_snapshot_to_signal_s",
              "first_b3_approval_to_signal_s",
              "signal_to_quote_s",
              "first_snapshot_to_quote_s")
    diagnostics = {}
    for key in stages:
        vals = [x[key] for x in result]
        diagnostics[key] = stats(vals)
        diagnostics[key]["negative_count"] = sum(v is not None and v < 0 for v in vals)
        diagnostics[key]["missing_count"] = sum(v is None for v in vals)
    return {
        "mode": "READ_ONLY_DISCOVERY_TO_PAPER_QUOTE_LATENCY",
        "summary": {
            "v32_positions": len(result),
            "positions_with_entry_quote": sum(bool(x["v32_quote_utc"]) for x in result),
            "positions_with_database_history": sum(x["snapshots"]>0 for x in result),
            "positions_with_retained_b3_approval": sum(bool(x["first_retained_b3_approval_utc"]) for x in result),
            "m1_tracked_positions": sum(x["m1_tracked"] for x in result),
            "timing": diagnostics
        },
        "positions": result,
        "limitations": [
            "First retained snapshot is NOT token launch/creation time.",
            "First retained B3 event depends on rolling state and retention.",
            "Quote time is hypothetical paper data, not a filled trade.",
            "Negative or missing deltas may indicate clock/source mismatch; not dropped.",
            "Measuring intervals alone does not prove faster discovery improves returns."
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--b3", default="/home/ubuntu/gta6-v63-test/b3_state.json")
    p.add_argument("--m1", default="/home/ubuntu/gta6-v63-test/m1_v3_state.json")
    p.add_argument("--v32", default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    p.add_argument("--db", default="/home/ubuntu/gta6-paper-vps/data/learning.db")
    p.add_argument("--output", default="research_discovery_latency.json")
    a=p.parse_args()
    data=[json.loads(Path(x).read_text()) for x in (a.b3,a.m1,a.v32)]
    with sqlite3.connect(Path(a.db).resolve().as_uri()+"?mode=ro", uri=True) as db:
        report=analyze(*data,db)
    target=Path(a.output)
    temp=target.with_name(target.name+".tmp")
    temp.write_text(json.dumps(report,indent=2)+"\n")
    temp.replace(target)
    print("SUMMARY",json.dumps(report["summary"],indent=2))
    print("SLOWEST QUOTE ENTRIES")
    rows=[x for x in report["positions"] if x["signal_to_quote_s"] is not None]
    for x in sorted(rows,key=lambda row:row["signal_to_quote_s"],reverse=True)[:10]:
        print(x["mint"][:12],"signal_to_quote_s=",x["signal_to_quote_s"],
              "first_snapshot_to_signal_s=",x["first_snapshot_to_signal_s"])
    print("Saved",target)


if __name__=="__main__":
    main()
