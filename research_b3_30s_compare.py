"""Compare isolated 30s B3 shadow against the existing B3 state. Read only."""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

APPROVED = "REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW"


def parse(stamp):
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def index(data):
    result = {}
    for e in data.get("events", []):
        if not isinstance(e, dict):
            continue
        key = (e.get("scanned_at"), e.get("mint"), e.get("pair"))
        at = parse(e.get("recorded_utc"))
        if not all(key) or at is None:
            continue
        if key not in result or at < parse(result[key]["recorded_utc"]):
            result[key] = e
    return result


def quantiles(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "median_seconds": None, "p90_seconds": None}
    return {"n": len(vals), "median_seconds": round(statistics.median(vals), 2),
            "p90_seconds": round(vals[round(0.9 * (len(vals) - 1))], 2)}


def compare(existing, pilot):
    a, b = index(existing), index(pilot)
    common = sorted(a.keys() & b.keys())
    paired = []
    contradictions = []
    for k in common:
        orig, new = a[k], b[k]
        scan = parse(k[0])
        old_t, new_t = parse(orig["recorded_utc"]), parse(new["recorded_utc"])
        if not scan or not old_t or not new_t:
            continue
        old_age = (old_t - scan).total_seconds()
        new_age = (new_t - scan).total_seconds()
        paired.append((old_age, new_age, old_age - new_age))
        if (orig.get("decision"), sorted(orig.get("reasons") or [])) != (
            new.get("decision"), sorted(new.get("reasons") or [])
        ):
            contradictions.append({"mint": k[1][:12], "scanned_at": k[0],
                                   "old_decision": orig.get("decision"),
                                   "pilot_decision": new.get("decision"),
                                   "old_reasons": orig.get("reasons"),
                                   "pilot_reasons": new.get("reasons")})
    unique_a = set(k[1] for k, e in a.items() if e.get("decision") == APPROVED)
    unique_b = set(k[1] for k, e in b.items() if e.get("decision") == APPROVED)
    return {
        "mode": "READ_ONLY_30S_B3_SHADOW_COMPARISON",
        "counts": {
            "existing_retained_events": len(a), "pilot_retained_events": len(b),
            "matched_same_snapshot": len(common),
            "pilot_only_snapshot_events": len(b.keys() - a.keys()),
            "existing_only_snapshot_events": len(a.keys() - b.keys()),
            "decision_disagreements": len(contradictions),
            "existing_approved_unique_mints_in_retained_history": len(unique_a),
            "pilot_approved_unique_mints_in_retained_history": len(unique_b),
            "shared_approved_unique_mints": len(unique_a & unique_b),
        },
        "paired_timing": {
            "existing_age": quantiles([v[0] for v in paired]),
            "pilot_age": quantiles([v[1] for v in paired]),
            "saving_existing_minus_pilot": quantiles([v[2] for v in paired]),
            "pilot_faster_same_snapshot": sum(v[2] > 0 for v in paired),
            "pilot_slower_same_snapshot": sum(v[2] < 0 for v in paired),
        },
        "disagreements_examples": contradictions[:20],
        "limitations": [
            "Only identical snapshot keys have causally comparable timing.",
            "First pilot run bootstraps and deliberately skips all earlier snapshots.",
            "Rolling events retention means missing events may not mean skipped processing.",
            "Earlier processing can change momentum calculations if future snapshots arrive;",
            "decision differences require inspection, not automatic adoption.",
            "Neither state proves actual token launch age or executed trade profitability.",
        ]
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--existing", default="/home/ubuntu/gta6-v63-test/b3_state.json")
    p.add_argument("--pilot", default="/home/ubuntu/gta6-v32-gate-pilot/b3_30s_state.json")
    p.add_argument("--output", default="research_b3_30s_comparison.json")
    a = p.parse_args()
    report = compare(json.loads(Path(a.existing).read_text()),
                     json.loads(Path(a.pilot).read_text()))
    dest = Path(a.output)
    temp = dest.with_name(dest.name + ".tmp")
    temp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temp.replace(dest)
    print(json.dumps({"counts": report["counts"], "paired_timing": report["paired_timing"]},
                     indent=2, ensure_ascii=False))
    print("Report saved:", dest)


if __name__ == "__main__":
    main()
