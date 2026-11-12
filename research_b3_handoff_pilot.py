"""Read-only B3 scheduling pilot: measure recorded observation -> B3 decision lag.

No services, files in existing runtime, requests, trading, or state are changed.
Scanned_at is a retained observation time, not token launch or paper-cycle end.
A 2-second hypothetical handoff after observation is an optimistic timing bound,
NOT a measured end-to-end achievable latency. Duplicate approval events
are reported separately from fresh unique-mint opportunities.
"""
import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PASS = "REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW"


def parse(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except ValueError:
        return None


def distribution(values):
    a = sorted(v for v in values if v is not None)
    if not a:
        return {"n": 0, "median_s": None, "p90_s": None, "max_s": None}
    p90 = a[round((len(a) - 1) * 0.9)]
    return {"n": len(a), "median_s": round(statistics.median(a), 2),
            "p90_s": round(p90, 2), "max_s": round(a[-1], 2)}


def simulate(b3, m1, v32, hypothetical_seconds=2):
    events = b3.get("events", [])
    tracked = m1.get("tracked", {})
    positions = v32.get("positions", {})
    if not isinstance(events, list) or not isinstance(tracked, dict) or not isinstance(positions, dict):
        raise ValueError("Unsupported state structure")
    seen_approved = set()
    rows = []
    for event in events:
        if not isinstance(event, dict):
            continue
        scan, recorded = parse(event.get("scanned_at")), parse(event.get("recorded_utc"))
        if scan is None or recorded is None:
            continue
        lag = (recorded - scan).total_seconds()
        if lag < 0:
            continue
        mint = event.get("mint")
        approved = event.get("decision") == PASS
        is_first_approved = bool(approved and mint not in seen_approved)
        if approved:
            seen_approved.add(mint)
        actual_within_quote_age = lag <= 360
        optimistic_lag = hypothetical_seconds
        row = {
            "mint": mint, "scan_utc": scan.isoformat(),
            "b3_recorded_utc": recorded.isoformat(),
            "decision": event.get("decision"),
            "current_lag_seconds": round(lag, 2),
            "optimistic_lag_seconds": optimistic_lag,
            "optimistic_saving_seconds": round(max(0, lag - optimistic_lag), 2),
            "first_retained_approval_for_mint": is_first_approved,
            "within_360s_at_b3": actual_within_quote_age,
            "m1_tracked": mint in tracked,
            "v32_position": mint in positions,
        }
        rows.append(row)
    approved = [r for r in rows if r["decision"] == PASS]
    distinct_approvals = [r for r in approved if r["first_retained_approval_for_mint"]]
    report = {
        "mode": "READ_ONLY_B3_SCHEDULING_COUNTERFACTUAL",
        "summary": {
            "timestamped_events": len(rows),
            "approved_events": len(approved),
            "distinct_mints_first_retained_approval": len(distinct_approvals),
            "repeated_approved_events": len(approved) - len(distinct_approvals),
            "all_events_observation_to_b3": distribution([r["current_lag_seconds"] for r in rows]),
            "approval_events_observation_to_b3": distribution([r["current_lag_seconds"] for r in approved]),
            "distinct_approval_observation_to_b3": distribution([r["current_lag_seconds"] for r in distinct_approvals]),
            "optimistic_savings_all": distribution([r["optimistic_saving_seconds"] for r in rows]),
            "distinct_approval_exceeded_360s_before_b3": sum(
                r["current_lag_seconds"] > 360 for r in distinct_approvals),
            "distinct_approval_with_m1": sum(r["m1_tracked"] for r in distinct_approvals),
            "distinct_approval_with_v32": sum(r["v32_position"] for r in distinct_approvals),
            "statuses_v32": dict(Counter(p.get("status", "UNKNOWN") for p in positions.values())),
        },
        "latest_distinct_approvals": distinct_approvals[-15:],
        "limitations": [
            "Counterfactual handoff is relative to scanned_at, not measured paper completion.",
            "A real post-paper trigger cannot run before the paper cycle completes.",
            "Retained B3 events may omit older records and may include duplicates.",
            "Even an earlier B3 decision does not imply Jupiter routing or fill.",
            "No production timers, state, databases, or trading are modified."
        ],
    }
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--b3", default="/home/ubuntu/gta6-v63-test/b3_state.json")
    p.add_argument("--m1", default="/home/ubuntu/gta6-v63-test/m1_v3_state.json")
    p.add_argument("--v32", default="/home/ubuntu/gta6-v63-test/m1_v3_2_state.json")
    p.add_argument("--hypothetical-handoff-seconds", type=int, default=2)
    p.add_argument("--output", default="research_b3_handoff_pilot.json")
    args = p.parse_args()
    if not 0 <= args.hypothetical_handoff_seconds <= 60:
        p.error("handoff must be 0..60 seconds")
    states = [json.loads(Path(path).read_text()) for path in
              (args.b3, args.m1, args.v32)]
    report = simulate(*states, hypothetical_seconds=args.hypothetical_handoff_seconds)
    out = Path(args.output)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(out)
    print("=== COUNTERFACTUAL HANDOFF SUMMARY ===")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print("=== RECENT UNIQUE APPROVALS ===")
    for r in report["latest_distinct_approvals"][-10:]:
        print(str(r["mint"])[:12], "actual_delay_s=", r["current_lag_seconds"],
              "optimistic_saving_s=", r["optimistic_saving_seconds"],
              "m1=", r["m1_tracked"], "v32=", r["v32_position"])
    print("Saved", out)


if __name__ == "__main__":
    main()
