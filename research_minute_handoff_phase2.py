"""Phase 2: in-memory B3 -> M1 handoff and duplicate-run safety audit.

Reads the existing isolated minute pilot DB (SQLite read-only) and B3/M1 JSON.
Copies BOTH JSON states into memory; does not execute production services,
call Jupiter, alter systemd, write files or persist any simulated events.
A hypothetical immediate M1 follow-up is run twice at the same fixed UTC
timestamp to check whether candidate identity and ignored keys are stable.
"""
import argparse
import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import research_b3_minute_safety_shadow as b3_module
import research_m1_minute_followup as m1_module

def verify_sources(b3,m1):
    if (not isinstance(b3,dict) or b3.get("version")!=1 or
        b3.get("mode")!="MINUTE_B3_SAFETY_SHADOW" or
        not isinstance(b3.get("cursor"),list) or len(b3["cursor"])!=3 or
        not isinstance(b3.get("events"),list) or
        not isinstance(b3.get("research_candidates"),list)):
        raise ValueError("Unexpected B3 state; fail closed")
    m1_module.validate(m1)

def simulate(b3_orig,m1_orig,db,now):
    """Run counterfactual on isolated deep copies, never persist any state."""
    verify_sources(b3_orig,m1_orig)
    b3=copy.deepcopy(b3_orig)
    m1=copy.deepcopy(m1_orig)
    before_candidates=set(m1["candidates"])
    before_ignored=set(m1["ignored"])
    b3_result=b3_module.advance(b3,db,now)
    first_added=m1_module.advance(m1,b3,db,now)
    after_first_candidates=set(m1["candidates"])
    after_first_ignored=set(m1["ignored"])
    # Same timestamp, same B3 input, same DB snapshot: repeat MUST not admit
    # existing signals or generate new candidate identities.
    second_added=m1_module.advance(m1,b3,db,now)
    after_second_candidates=set(m1["candidates"])
    after_second_ignored=set(m1["ignored"])
    stable=(second_added==0 and after_first_candidates==after_second_candidates
            and after_first_ignored==after_second_ignored)
    new_ids=after_first_candidates-before_candidates
    if first_added!=len(new_ids):
        raise ValueError("M1 admitted count differs from unique new identities")
    if not stable:
        raise ValueError("Duplicate M1 follow-up admitted additional records")
    if b3_result["new_research_candidates"]<0:
        raise ValueError("Invalid B3 result")
    return {
        "mode":"READ_ONLY_PHASE2_HANDOFF_SAFETY_AUDIT",
        "checked_utc":now.isoformat(),
        "simulation_only":True,
        "checks":{"state_modes_valid":True,
                  "b3_advanced_only_in_memory":True,
                  "m1_duplicate_run_candidate_idempotent":stable,
                  "no_new_candidates_on_duplicate":second_added==0},
        "b3_new_minute_observations_simulated":b3_result["added"],
        "b3_new_research_candidates_simulated":b3_result["new_research_candidates"],
        "m1_unique_new_candidates_if_immediate":first_added,
        "m1_new_ignored_keys_if_immediate":len(after_first_ignored-before_ignored),
        "m1_duplicated_run_new_candidates":second_added,
        "m1_existing_candidate_count":len(before_candidates),
        "limitations":[
            "Simulation uses already available database observations as of the audit, not a prospective event-time snapshot.",
            "Candidate idempotency at a fixed timestamp does not guarantee concurrency safety.",
            "Does not measure actual scheduling improvement or Jupiter quote speed.",
            "No systemd timer/service changes, wallet operations or real trades.",
            "To deploy an active trigger later, coordinate timers and a shared lock; current M1 state lock alone is insufficient for a B3->M1 service orchestration guarantee."]}

def audit(b3_path,m1_path,db_path,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC-aware now required")
    b3=json.loads(Path(b3_path).read_text(encoding="utf-8"))
    m1=json.loads(Path(m1_path).read_text(encoding="utf-8"))
    # Open DB read only; SQLite URI prevents accidental writes.
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return simulate(b3,m1,db,now)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--b3",default=b3_module.STATE_DEFAULT)
    ap.add_argument("--m1",default=m1_module.STATE)
    ap.add_argument("--db",default=b3_module.DB_DEFAULT)
    args=ap.parse_args()
    report=audit(args.b3,args.m1,args.db)
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
