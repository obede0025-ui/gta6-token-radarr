"""Single-command, read-only end-to-end GTA6 research verification.

Checks actual VPS services, legacy and minute data, B3 -> M1 -> Jupiter
handoffs, quote-only outcomes, selection A/B and regression tests. Does not
call market APIs, restart timers, change DB/state, or place trades.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from research_pipeline_integration_audit import run as integration_run
from research_pipeline_quality_audit import audit as quality_audit
from research_jupiter_minute_evaluation import report as quote_report
from research_selection_shadow import report as selection_report
from research_legacy_v32_gap_explain import explain as legacy_gap_explain

ROOT=Path("/home/ubuntu")
PILOT=ROOT/"gta6-v32-gate-pilot"
SERVICES=[
    "gta6-minute-market-pilot.service",
    "gta6-b3-minute-safety-shadow.service",
    "gta6-m1-minute-research.service",
]
TEST_PATTERNS=[
    "test_research_minute_market_pilot.py",
    "test_research_selection_shadow.py",
    "test_research_pipeline_quality_audit.py",
    "test_research_jupiter_horizon_quote.py",
    "test_research_jupiter_minute_evaluation.py",
    "test_research_one_shot_system_test.py",
    "test_research_legacy_v32_gap_explain.py",
]
ORDER={"PASS":0,"INFO":0,"WAITING":1,"WARN":2,"FAIL":3}

def parse_stamp(value):
    if not isinstance(value,str):return None
    try:
        d=datetime.fromisoformat(value.replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,OverflowError):return None

def read_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)

def check(results,name,status,detail,metric=None):
    results.append({"name":name,"status":status,"detail":detail,
                    **({"metric":metric} if metric is not None else {})})

def freshness(results,name,ts,now,threshold=180):
    stamp=parse_stamp(ts)
    age=(now-stamp).total_seconds() if stamp else None
    status="PASS" if age is not None and -30<=age<=threshold else "WARN"
    check(results,name,status,
          "age_seconds="+str(round(age,1) if age is not None else "UNKNOWN"),
          {"latest_utc":ts,"age_seconds":round(age,1) if age is not None else None})

def services(results,root):
    if Path(root)!=ROOT:
        check(results,"services","INFO","Skipped for non-default test root")
        return
    for unit in SERVICES:
        try:
            p=subprocess.run(["systemctl","show",unit,"-p","Result","-p",
                              "ExecMainStatus","--no-pager"],
                             capture_output=True,text=True,timeout=8,check=False)
            vals=dict(x.split("=",1) for x in p.stdout.splitlines() if "=" in x)
            result=vals.get("Result")
            rc=vals.get("ExecMainStatus")
            status="PASS" if p.returncode==0 and result=="success" and rc=="0" else "WARN"
            check(results,"service:"+unit,status,
                  "result="+str(result)+", exit="+str(rc))
        except (OSError,subprocess.TimeoutExpired) as exc:
            check(results,"service:"+unit,"WARN",type(exc).__name__)

def run_tests(results,root,python,enabled=True):
    if not enabled:
        check(results,"regression_tests","INFO","Skipped via --skip-tests")
        return
    if not (Path(root)/"gta6-v32-gate-pilot"/"tests").is_dir():
        check(results,"regression_tests","FAIL","Test folder missing")
        return
    for pattern in TEST_PATTERNS:
        test_path=Path(root)/"gta6-v32-gate-pilot"/"tests"/pattern
        if not test_path.is_file():
            check(results,"test:"+pattern,"WARN","Test not found")
            continue
        try:
            p=subprocess.run([str(python),"-m","unittest","discover","-s","tests",
                              "-p",pattern,"-v"],
                             cwd=str(Path(root)/"gta6-v32-gate-pilot"),
                             capture_output=True,text=True,timeout=90,check=False)
            output=(p.stdout+"\n"+p.stderr).strip().splitlines()
            tail=" | ".join(output[-3:])[-350:]
            check(results,"test:"+pattern,"PASS" if p.returncode==0 else "FAIL",
                  tail or "No output")
        except (OSError,subprocess.TimeoutExpired) as exc:
            check(results,"test:"+pattern,"FAIL",type(exc).__name__)

def sqlite_integrity(results,path,name):
    if not path.is_file():
        check(results,name,"FAIL","Database not found: "+str(path))
        return
    try:
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,
                             timeout=3) as db:
            v=db.execute("PRAGMA quick_check").fetchone()[0]
            check(results,name,"PASS" if v=="ok" else "FAIL",
                  "SQLite quick_check="+str(v))
    except sqlite3.Error as exc:
        check(results,name,"FAIL",type(exc).__name__+": "+str(exc)[:140])

def evaluate(root=ROOT,now=None,with_tests=True,python=sys.executable):
    start=time.monotonic()
    now=now or datetime.now(timezone.utc)
    root=Path(root)
    pilot=root/"gta6-v32-gate-pilot"
    checks=[]
    metrics={}
    # Tests are independent of API and persisted state.
    run_tests(checks,root,python,with_tests)
    services(checks,root)
    sqlite_integrity(checks,root/"gta6-paper-vps/data/learning.db","sqlite:learning")
    sqlite_integrity(checks,pilot/"minute_market_pilot.sqlite","sqlite:minute")
    sqlite_integrity(checks,pilot/"selection_shadow_comparison.sqlite","sqlite:ab_comparison")
    try:
        full=integration_run(now=now,root=root)
        counts=full.get("summary",{})
        metrics["integration_summary"]=counts
        for item in full.get("checks",[]):
            status=item.get("status","WARN")
            if status not in ORDER:status="WARN"
            check(checks,"integration:"+item.get("check","unknown"),status,
                  str(item.get("detail",""))[:350])
    except Exception as exc:
        check(checks,"integration_audit","FAIL",type(exc).__name__+": "+str(exc)[:160])

    try:
        legacy=legacy_gap_explain(
            read_json(root/"gta6-v63-test/m1_v3_state.json"),
            read_json(root/"gta6-v63-test/m1_v3_2_state.json"))
        metrics["legacy_v32_gap_explanation"]=legacy
        check(checks,"legacy_v32_missing_explained",legacy["status"],
              "missing="+str(legacy["missing_total"])+
              ", classified="+json.dumps(legacy["classified"],sort_keys=True))
    except Exception as exc:
        check(checks,"legacy_v32_gap_diagnostic","WARN",
              type(exc).__name__+": "+str(exc)[:160])
    try:
        b3=read_json(pilot/"b3_minute_safety_shadow_state.json")
        m1=read_json(pilot/"m1_minute_research_state.json")
        jupiter=read_json(pilot/"jupiter_minute_quotes_state.json")
        q=quality_audit(pilot/"minute_market_pilot.sqlite",b3,m1,jupiter,now)
        metrics["market"]=q["market"]
        metrics["signals"]=q["signals"]
        metrics["quotes"]=q["quotes"]
        for item in q["checks"]:
            status=item["status"]
            if status=="WAITING_FOR_NEW_ENTRY":status="WAITING"
            check(checks,"quality:"+item["name"],status,
                  json.dumps(item["detail"],sort_keys=True))
        freshness(checks,"freshness:b3",b3.get("last_checked_utc"),now)
        freshness(checks,"freshness:m1",m1.get("last_checked_utc"),now)
        freshness(checks,"freshness:jupiter",jupiter.get("last_checked_utc"),now)
        candidates=m1.get("candidates",{})
        positions=jupiter.get("positions",{})
        skipped=jupiter.get("skipped",{})
        cutoff=parse_stamp(jupiter.get("started_utc"))
        new={ident for ident,c in candidates.items() if isinstance(c,dict)
             and cutoff and parse_stamp(c.get("registered_utc"))
             and parse_stamp(c.get("signal_utc"))
             and parse_stamp(c["registered_utc"])>cutoff
             and parse_stamp(c["signal_utc"])>cutoff}
        outstanding=new-set(positions)-set(skipped)
        check(checks,"handoff:m1_to_jupiter",
              "WARN" if outstanding else ("PASS" if new else "WAITING"),
              "post-bootstrap="+str(len(new))+", unaccounted="+str(len(outstanding)))
        eval_data=quote_report(jupiter)
        metrics["quote_evaluation_summary"]=eval_data.get("summary",{})
        metrics["quote_research_checks"]=eval_data.get("research_checks",{})
        metrics["quote_limitations"]=eval_data.get("limitations",[])
        samples=eval_data.get("summary",{}).get("positions",0)
        check(checks,"quote_outcome_evaluation",
              "PASS" if samples else "WAITING",
              "quote-only positions="+str(samples)+", never executable trades")
        check(checks,"strategy_proof","WARN",
              "Quote scenarios do not establish real-world profitable execution")
    except Exception as exc:
        check(checks,"minute_end_to_end","FAIL",
              type(exc).__name__+": "+str(exc)[:240])
    try:
        ab=selection_report(path=pilot/"selection_shadow_comparison.sqlite",
                            hours=24,now=now)
        metrics["selection_ab"]=ab
        cycles=ab.get("cycles_successful",0)
        errors=ab.get("cycles_failed",0)
        check(checks,"selection_ab_health",
              "WARN" if errors else ("PASS" if cycles else "WAITING"),
              "successful="+str(cycles)+", failed="+str(errors))
        first=parse_stamp(ab.get("first_cycle_utc"))
        last=parse_stamp(ab.get("last_cycle_utc"))
        span=(last-first).total_seconds()/3600 if first and last else 0
        complete=cycles>=60 and span>=23
        base=ab.get("base_filter_without_age_or_momentum",{})
        diff=base.get("diff",0)
        better=base.get("cycles_proposed_better",0)
        worse=base.get("cycles_proposed_worse",0)
        check(checks,"selection_ab_evidence",
              "PASS" if complete else "WAITING",
              "hours_covered="+str(round(span,2))+", successful="+str(cycles)+
              ", base_pass_diff="+str(diff)+", cycles_better="+str(better)+
              ", cycles_worse="+str(worse)+
              "; no automatic switch or return claim")
    except Exception as exc:
        check(checks,"selection_ab","FAIL",
              type(exc).__name__+": "+str(exc)[:160])
    counts=dict(Counter(item["status"] for item in checks))
    # A healthy quote-only research stack may have intentional legacy GAPs
    # and limited data. Never turn that into profitability certification.
    verdict=("FAIL" if counts.get("FAIL") else
             "NEEDS_ATTENTION" if counts.get("WARN") else
             "COLLECTING_EVIDENCE" if counts.get("WAITING") else
             "TECHNICALLY_HEALTHY_NOT_PROFIT_VALIDATED")
    return {"mode":"GTA6_ONE_SHOT_END_TO_END_AUDIT",
            "checked_utc":now.isoformat(),"elapsed_seconds":round(time.monotonic()-start,2),
            "verdict":verdict,"summary":counts,
            "checks":checks,"metrics":metrics,
            "policy":{"read_only":True,"no_external_apis":True,
                      "no_trade_execution":True,"no_service_changes":True,
                      "no_database_writes":True},
            "recommendations":[
                "FAIL means a broken or untestable component; inspect the matching check.",
                "WARN means data/architecture limitations, not a guaranteed outage.",
                "WAITING means we need prospective samples, not manual token checks.",
                "Do not activate proposed selection on count improvements alone.",
                "For full comparative TP/SL, proposed tokens need their own forward market/quote histories."
            ]}

def readable(result):
    lines=[
        "=== GTA6 END-TO-END TEST ===",
        "Checked UTC: "+result["checked_utc"],
        "VERDICT: "+result["verdict"],
        "CHECKS: "+json.dumps(result["summary"],sort_keys=True),
        ""]
    for c in result["checks"]:
        lines.append("["+c["status"]+"] "+c["name"]+" :: "+str(c["detail"]))
    ab=result["metrics"].get("selection_ab",{})
    if ab:
        lines.extend(["","=== SELECTIE A/B ===",
                      "Cycles OK: "+str(ab.get("cycles_successful"))+
                      " | Failed: "+str(ab.get("cycles_failed")),
                      "Known-liquidity delta: "+str(ab.get("known_liquidity",{}).get("diff")),
                      "Volume>=250 delta: "+str(ab.get("volume_ge_250",{}).get("diff")),
                      "Base-pass delta: "+str(ab.get("base_filter_without_age_or_momentum",{}).get("diff"))])
    gap=result["metrics"].get("legacy_v32_gap_explanation",{})
    if gap:
        lines.extend(["","=== LEGACY V3.2 COVERAGE ===",
                      "Missing: "+str(gap["missing_total"])+
                      " | Categories: "+json.dumps(gap["classified"],sort_keys=True)])
    quotes=result["metrics"].get("quote_evaluation_summary",{})
    if quotes:
        lines.extend(["","=== JUPITER RESEARCH ===",
                      json.dumps(quotes,ensure_ascii=False,sort_keys=True)])
    lines.extend(["","NOTE: Read-only; quote observations are not real fills or P&L."])
    return "\n".join(lines)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",default=str(ROOT))
    p.add_argument("--skip-tests",action="store_true")
    p.add_argument("--json",action="store_true",help="Complete machine-readable report")
    p.add_argument("--python",default=sys.executable)
    args=p.parse_args()
    result=evaluate(root=Path(args.root),with_tests=not args.skip_tests,
                    python=args.python)
    print(json.dumps(result,indent=2,ensure_ascii=False) if args.json else readable(result))
    if result["summary"].get("FAIL",0):sys.exit(2)

if __name__=="__main__":main()
