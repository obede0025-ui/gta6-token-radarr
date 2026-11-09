"""Read-only latency diagnostics for B3 -> M1 V3 -> Jupiter V3.2.

Do not mistake B3 timestamp for the token's launch time, or a first
market observation for an exact process registration timestamp.
"""
import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path


def parse(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def delta_seconds(later, earlier):
    a, b = parse(later), parse(earlier)
    return round((a - b).total_seconds(), 1) if a and b else None


def percentile(values, percent):
    values = sorted(v for v in values if v is not None and v >= 0)
    if not values:
        return None
    index = (len(values) - 1) * percent / 100
    lo, hi = int(index), min(int(index) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (index - lo), 1)


def diagnose(b3, m1, v32):
    events = b3.get("events")
    tracked = m1.get("tracked")
    positions = v32.get("positions")
    if not isinstance(events, list) or not isinstance(tracked, dict) or not isinstance(positions, dict):
        raise ValueError("Unsupported state data")
    first_approved = {}
    approved_total = 0
    for event in events:
        if event.get("decision") != "REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW":
            continue
        approved_total += 1
        mint = event.get("mint")
        if not isinstance(mint, str):
            continue
        stamp = event.get("scanned_at")
        if parse(stamp) and (mint not in first_approved or parse(stamp) < parse(first_approved[mint])):
            first_approved[mint] = stamp
    rows = []
    for mint, item in sorted(tracked.items(), key=lambda row: row[1].get("signal", "")):
        signal = item.get("signal")
        market_observations = [s.get("at") for s in item.get("samples", []) if isinstance(s, dict) and parse(s.get("at"))]
        market_observations.sort(key=parse)
        market_first = market_observations[0] if market_observations else None
        quote_position = positions.get(mint)
        entry_time = quote_position.get("entry_quote_utc") if quote_position else None
        first_sell = None
        if quote_position:
            sell_times = [x.get("checked_utc") for x in quote_position.get("exit_quotes", []) if parse(x.get("checked_utc"))]
            if sell_times:
                first_sell = min(sell_times, key=parse)
        rows.append({
            "mint_prefix":mint[:12],
            "b3_first_approval_utc":first_approved.get(mint),
            "m1_signal_utc":signal,
            "b3_recorded_utc":item.get("b3_recorded_utc"),
            "m1_registered_utc":item.get("registered_utc"),
            "b3_to_m1_registration_seconds":delta_seconds(item.get("registered_utc"),item.get("b3_recorded_utc")),
            "m1_to_v32_first_seen_seconds":delta_seconds(quote_position.get("first_seen_utc") if quote_position else None,item.get("registered_utc")),
            "m1_status":item.get("status"),
            "m1_first_market_observation_utc":market_first,
            "b3_to_first_m1_observation_seconds":delta_seconds(market_first, first_approved.get(mint)),
            "m1_observation_count":len(item.get("samples", [])),
            "v32_status":quote_position.get("status") if quote_position else "NOT_RECORDED",
            "v32_entry_lag_seconds":delta_seconds(entry_time, signal),
            "v32_first_exit_lag_seconds":delta_seconds(first_sell, signal),
            "v32_recorded_initial_lag_seconds":quote_position.get("entry_lag_seconds") if quote_position else None,
            "v32_exit_quote_count":len(quote_position.get("exit_quotes", [])) if quote_position else 0,
        })
    delays = [r["b3_to_first_m1_observation_seconds"] for r in rows]
    entry = [r["v32_entry_lag_seconds"] for r in rows]
    status_counts = {}
    for p in positions.values():
        status = p.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "mode":"READ_ONLY_LATENCY_AUDIT",
        "summary":{
            "b3_approved_events":approved_total,
            "b3_unique_tokens":len(first_approved),
            "m1_tracked":len(tracked),
            "v32_recorded":len(positions),
            "v32_statuses":status_counts,
            "b3_to_m1_registration_median_seconds":percentile([r["b3_to_m1_registration_seconds"] for r in rows],50),
            "m1_to_v32_first_seen_median_seconds":percentile([r["m1_to_v32_first_seen_seconds"] for r in rows],50),
            "b3_to_m1_first_observation_median_seconds":percentile(delays,50),
            "b3_to_m1_first_observation_p90_seconds":percentile(delays,90),
            "m1_to_v32_entry_quote_median_seconds":percentile(entry,50),
            "m1_to_v32_entry_quote_p90_seconds":percentile(entry,90),
            "v32_entries_quoted":sum(p.get("entry_token_raw") is not None for p in positions.values()),
            "v32_exit_quotes":sum(len(p.get("exit_quotes", [])) for p in positions.values()),
        },
        "observations":rows,
        "limitations":[
            "B3 approval is not token discovery/launch time: B3 scans ~5 minutes.",
            "Precise processing latencies apply only to events created AFTER timestamp instrumentation.",
            "M1 first market sample is NOT exact receipt/registration time.",
            "Existing records may hold a newer/shortened samples array (max 20), biasing measured latency upward.",
            "Missing quotes do not imply Jupiter failure. Missed/old signals are intentionally excluded.",
            "A price trigger in M1 V3 stops further sampling; do not interpret it as a real fill.",
            "No secrets, real executions or external API calls are used.",
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--b3", required=True)
    p.add_argument("--m1", required=True)
    p.add_argument("--v32", required=True)
    p.add_argument("--output")
    args = p.parse_args()
    report = diagnose(*(json.loads(Path(path).read_text(encoding="utf-8"))
                        for path in (args.b3, args.m1, args.v32)))
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(target)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
