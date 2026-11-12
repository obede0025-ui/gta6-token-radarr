"""Read-only integration health audit for the existing GTA6 research stack.

Never starts/restarts units, calls trading APIs, or changes state/SQLite.
Run on the Ubuntu VPS with access to the existing on-disk states.
"""
import argparse
import json
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/ubuntu")
PROD = ROOT / "gta6-v63-test"
PILOT = ROOT / "gta6-v32-gate-pilot"
DATABASE = ROOT / "gta6-paper-vps/data/learning.db"

UNITS = {
    "collector": "gta6-paper.timer",
    "b3": "gta6-b3-shadow.timer",
    "b3_30s": "gta6-b3-30s-shadow.timer",
    "m1": "gta6-m1-v3.timer",
    "v32": "gta6-m1-v32.timer",
    "v32_gate": "gta6-v32-gate-pilot.timer",
    "volume": "gta6-volume-3000-shadow.timer",
    "pipeline": "gta6-paper-pipeline-v1.timer",
}

def stamp(value):
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return x.astimezone(timezone.utc) if x.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None

def elapsed(value, now):
    x = stamp(value)
    return round((now-x).total_seconds()) if x else None

def state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, type(exc).__name__

def unit_property(unit, prop):
    try:
        r = subprocess.run(["systemctl", "show", unit, "-p", prop, "--value"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"

def add(checks, name, status, detail):
    checks.append({"check": name, "status": status, "detail": detail})

def run(now=None, root=ROOT):
    now = now or datetime.now(timezone.utc)
    checks = []
    prod, pilot = root/"gta6-v63-test", root/"gta6-v32-gate-pilot"
    dbfile = root/"gta6-paper-vps/data/learning.db"
    if root == ROOT:
        for label, unit in UNITS.items():
            active = unit_property(unit, "ActiveState")
            add(checks, "timer_"+label, "PASS" if active=="active" else "WARN",
                f"{unit}: {active}")
        service = "gta6-fast-monitor.service"
        active = unit_property(service, "ActiveState")
        execstart = unit_property(service, "ExecStart")
        add(checks, "fast_monitor", "PASS" if active == "active" else "WARN",
            f"{active}, 60s configured={('--interval 60' in execstart)}")
    try:
        with sqlite3.connect(dbfile.resolve().as_uri()+"?mode=ro", uri=True) as db:
            cursor = db.execute("SELECT COUNT(*), MAX(scanned_at) FROM snapshots").fetchone()
        count, last = cursor
        age = elapsed(last, now)
        add(checks, "market_db", "PASS" if age is not None and -30<=age<=600 else "WARN",
            f"{count} snapshots, latest={last}, age_seconds={age}")
    except (sqlite3.Error, OSError) as exc:
        last = None
        add(checks, "market_db", "FAIL", type(exc).__name__)

    paths = {
        "b3": prod/"b3_state.json",
        "b3_30s": pilot/"b3_30s_state.json",
        "m1": prod/"m1_v3_state.json",
        "v32": prod/"m1_v3_2_state.json",
        "volume": pilot/"volume_3000_shadow_state.json",
        "pipeline": pilot/"paper_pipeline_v1_state.json",
    }
    data = {}
    for label, path in paths.items():
        obj, err = state(path)
        data[label] = obj
        if err:
            add(checks, "state_"+label, "FAIL", str(path)+": "+err)
            continue
        if label.startswith("b3"):
            events = obj.get("events", [])
            timekey = obj.get("updated_utc")
            detail = f"{len(events)} retained events, approvals={sum(e.get('decision')=='REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW' for e in events)}, updated={timekey}"
        elif label == "m1":
            detail = f"{len(obj.get('tracked', {}))} tracked"
        elif label == "v32":
            positions = obj.get("positions", {})
            detail = f"{len(positions)} positions, quoted_entries={sum(bool(p.get('entry_quote_utc')) for p in positions.values())}"
        else:
            detail = f"{len(obj.get('candidates', {}))} prospective candidates, last_checked={obj.get('last_checked_utc')}"
        add(checks, "state_"+label, "PASS", detail)

    b3, fast, m1, v32 = (data.get(x) for x in ("b3","b3_30s","m1","v32"))
    if b3 and fast:
        a = b3.get("events", [])
        b = fast.get("events", [])
        keys_a = {(e.get("scanned_at"),e.get("mint"),e.get("pair")):e.get("decision") for e in a}
        keys_b = {(e.get("scanned_at"),e.get("mint"),e.get("pair")):e.get("decision") for e in b}
        intersection = set(keys_a)&set(keys_b)
        mismatches = sum(keys_a[k]!=keys_b[k] for k in intersection)
        add(checks, "b3_comparison", "WARN" if mismatches else "PASS",
            f"shared_snapshots={len(intersection)}, decision_mismatches={mismatches}, old_only={len(keys_a)-len(intersection)}, fast_only={len(keys_b)-len(intersection)}")
        add(checks,"fast_b3_handoff","GAP",
            "30-second B3 uses isolated state; existing M1 reads original b3_state.json")
    if b3 and m1:
        approved = [e for e in b3.get("events",[]) if e.get("decision")=="REQUIRES_FURTHER_SAFETY_AND_QUOTE_REVIEW"]
        approved_mints = {e["mint"] for e in approved if e.get("mint")}
        tracked = set(m1.get("tracked",{}))
        add(checks,"b3_to_m1","PASS" if approved_mints <= tracked else "WARN",
            f"unique_approved_in_retained_B3={len(approved_mints)}, registered_in_M1={len(approved_mints & tracked)}, missing={len(approved_mints-tracked)}; limited by retained history")
    if m1 and v32:
        tracked = set(m1.get("tracked",{}))
        quoted = set(v32.get("positions",{}))
        add(checks, "m1_to_v32", "PASS" if tracked <= quoted else "WARN",
            f"tracked_m1={len(tracked)}, represented_in_v32={len(tracked & quoted)}, missing={len(tracked-quoted)}; may be expected after bootstrapping")
    if m1 and data.get("pipeline") and data.get("volume"):
        threshold = stamp(data["pipeline"].get("started_utc"))
        newer = [mint for mint,item in m1.get("tracked",{}).items()
                 if threshold and stamp(item.get("registered_utc")) and
                 stamp(item["registered_utc"]) > threshold]
        ledger = set(data["pipeline"].get("candidates",{}))
        add(checks, "m1_to_paper_ledger",
            "PASS" if set(newer)<=ledger else "WARN",
            f"new_M1_since_pilot={len(newer)}, ledger={len(ledger)}, missing={len(set(newer)-ledger)}")
        threshold = stamp(data["volume"].get("started_utc"))
        newer = [mint for mint,item in m1.get("tracked",{}).items()
                 if threshold and stamp(item.get("registered_utc")) and
                 stamp(item["registered_utc"]) > threshold]
        ledger = set(data["volume"].get("candidates",{}))
        add(checks, "m1_to_volume_ledger",
            "PASS" if set(newer)<=ledger else "WARN",
            f"new_M1_since_volume={len(newer)}, ledger={len(ledger)}, missing={len(set(newer)-ledger)}")
    add(checks, "fast_monitor_handoff", "GAP",
        "Fast-monitor logs observations but does not append to learning.db or feed B3")
    add(checks, "paper_only_integrity", "PASS",
        "Audit reads existing data only; quote observations are not execution fills")
    counts = dict(Counter(x["status"] for x in checks))
    return {"mode":"GTA6_READ_ONLY_INTEGRATION_AUDIT",
            "checked_utc":now.isoformat(), "summary":counts, "checks":checks}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root",default=str(ROOT))
    args=p.parse_args()
    print(json.dumps(run(root=Path(args.root)),indent=2,ensure_ascii=False))

if __name__=="__main__":
    main()
