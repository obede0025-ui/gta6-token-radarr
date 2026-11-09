"""V6.3 isolated quote-risk audit. No network, wallet, trading or portfolio mutations."""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

def utc_time(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            return None
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def classify(position, stake, stale):
    status = str(position.get("status") or "UNKNOWN")
    raw = position.get("estimated_exit_usdc")
    if stale:
        return "STALE_QUOTE", None
    if status != "QUOTE_AVAILABLE":
        return "UNAVAILABLE_" + status[:40], None
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0 or stake <= 0:
            return "INVALID_QUOTE", None
        loss = (Decimal(1) - value / stake) * 100
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return "INVALID_QUOTE", None
    if loss > 70:
        severity = "CRITICAL"
    elif loss > 30:
        severity = "HIGH"
    elif loss > 10:
        severity = "WARNING"
    else:
        severity = "NORMAL"
    return severity, round(float(loss), 3)

def audit(report, max_age_seconds=900, now=None):
    now = now or datetime.now(timezone.utc)
    checked = utc_time(report.get("checked_utc"))
    stale = checked is None or (now - checked).total_seconds() > max_age_seconds or checked > now
    try:
        stake = Decimal(str(report.get("stake_usdc", "25")))
        if not stake.is_finite() or stake <= 0:
            raise ValueError("Bad stake")
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid quote report stake")
    positions = []
    for item in report.get("positions") or []:
        for position in item.get("positions") or []:
            status, loss = classify(position, stake, stale)
            positions.append({
                "mint": item.get("mint"),
                "strategy": position.get("strategy"),
                "quote_status": position.get("status"),
                "estimated_exit_usdc": position.get("estimated_exit_usdc"),
                "estimated_loss_pct": loss,
                "risk": status,
            })
    return {
        "mode": "QUOTE_EXIT_RISK_AUDIT_READ_ONLY",
        "checked_utc": report.get("checked_utc"),
        "audit_utc": now.isoformat(timespec="seconds"),
        "source_status": report.get("status"),
        "stale": stale,
        "positions": positions,
        "counts": {name: sum(p["risk"] == name for p in positions)
                   for name in sorted(set(p["risk"] for p in positions))},
        "warning": "Indicative quotes only; unknown/stale is NOT a confirmed loss or sellability."
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.max_age_seconds < 1:
        parser.error("max-age-seconds must be positive")
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = audit(report, args.max_age_seconds)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp")
    temp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temp.replace(destination)
    print(json.dumps({"stale": result["stale"], "counts": result["counts"]}))

if __name__ == "__main__":
    main()
