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
from research_legacy_v32_gap_explain import explain as explain_v32

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
    "minute_market": "gta6-minute-market-pilot.timer",
    "minute_b3": "gta6-b3-minute-safety-shadow.timer",
    "minute_m1": "gta6-m1-minute-research.timer",
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
        explanation=explain_v32(m1,v32)
        add(checks, "m1_to_v32", explanation["status"],
            "tracked_m1="+str(len(tracked))+", represented_in_v32="+str(len(tracked & quoted))+
            ", missing="+str(len(tracked-quoted))+", classified="+str(explanation["classified"]))
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

    # Isolated new minute pipeline: report freshness and handoffs in the same
    # audit rather than adding another scanner or background service.
    minute_db = pilot/"minute_market_pilot.sqlite"
    try:
        with sqlite3.connect(minute_db.resolve().as_uri()+"?mode=ro",uri=True) as db:
            total,mints,last_minute=db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT mint),MAX(observed_at) FROM minute_observations"
            ).fetchone()
            statuses=dict(db.execute(
                "SELECT status,COUNT(*) FROM minute_observations WHERE observed_at=? GROUP BY status",
                (last_minute,)).fetchall()) if last_minute else {}
        age=elapsed(last_minute,now)
        add(checks,"minute_market_db","PASS" if age is not None and -30<=age<=180 else "WARN",
            f"{total} samples, {mints} mints, latest={last_minute}, age_seconds={age}, statuses={statuses}")
    except (sqlite3.Error,OSError) as exc:
        add(checks,"minute_market_db","FAIL",type(exc).__name__)
    minute_paths={
        "minute_b3":pilot/"b3_minute_safety_shadow_state.json",
        "minute_m1":pilot/"m1_minute_research_state.json",
        "minute_jupiter":pilot/"jupiter_minute_quotes_state.json",
    }
    minute_data={}
    for label,path in minute_paths.items():
        obj,err=state(path)
        minute_data[label]=obj
        if err:
            add(checks,"state_"+label,"FAIL",str(path)+": "+err)
            continue
        timekey=obj.get("last_checked_utc")
        age=elapsed(timekey,now)
        count=len(obj.get("research_candidates",[])) if label=="minute_b3" else len(obj.get("candidates",{})) if label=="minute_m1" else len(obj.get("positions",{}))
        add(checks,"state_"+label,"PASS" if age is not None and -30<=age<=180 else "WARN",
            f"{count} candidates/positions, last_checked={timekey}, age_seconds={age}")
    minute_b3=minute_data.get("minute_b3")
    minute_m1=minute_data.get("minute_m1")
    minute_jupiter=minute_data.get("minute_jupiter")
    if minute_b3 and minute_m1:
        cutoff=stamp(minute_m1.get("started_utc"))
        signals=[c for c in minute_b3.get("research_candidates",[]) if
                 isinstance(c,dict) and cutoff and stamp(c.get("signal_utc"))
                 and stamp(c.get("signal_utc"))>cutoff]
        registered=set(minute_m1.get("candidates",{}))
        ignored=set(minute_m1.get("ignored",{}))
        def identifier(c):
            return json.dumps([c["mint"],c["pair"],c["signal_utc"]],separators=(",",":"))
        eligible={identifier(c) for c in signals if all(isinstance(c.get(k),str) for k in ("mint","pair","signal_utc"))}
        accounted=len(eligible & (registered|ignored))
        b3_handoff_status=("WAITING_FOR_SIGNAL" if not eligible else
                           "PASS" if accounted==len(eligible) else "WARN")
        add(checks,"minute_b3_to_m1",b3_handoff_status,
            f"post_bootstrap_B3_signals={len(eligible)}, tracked_or_explained={accounted}, unaccounted={len(eligible)-accounted}")
    if minute_m1 and minute_jupiter:
        cutoff=stamp(minute_jupiter.get("started_utc"))
        fresh={key for key,c in minute_m1.get("candidates",{}).items()
               if isinstance(c,dict) and cutoff and stamp(c.get("signal_utc"))
               and stamp(c.get("registered_utc")) and
               stamp(c.get("signal_utc"))>cutoff and
               stamp(c.get("registered_utc"))>cutoff}
        accounted=set(minute_jupiter.get("positions",{}))|set(minute_jupiter.get("skipped",{}))
        quote_handoff_status=("WAITING_FOR_SIGNAL" if not fresh else
                              "PASS" if fresh<=accounted else "WARN")
        add(checks,"minute_m1_to_jupiter",quote_handoff_status,
            f"post_bootstrap_M1_signals={len(fresh)}, quoted_or_explained={len(fresh & accounted)}, unaccounted={len(fresh-accounted)}")
        positions=minute_jupiter.get("positions",{})
        entry_quotes=sum(p.get("entry_token_raw") is not None for p in positions.values())
        exit_quotes=sum(len(p.get("exit_quotes",[])) for p in positions.values())
        quote_status=("WAITING_FOR_SIGNAL" if not positions else
                      "PASS" if entry_quotes and exit_quotes else "WARN")
        add(checks,"minute_jupiter_quote_coverage",quote_status,
            f"quote_candidates={len(positions)}, entry_quotes={entry_quotes}, exit_quote_observations={exit_quotes}; no claims of executable fills")
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
