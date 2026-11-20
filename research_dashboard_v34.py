"""Build a self-contained read-only HTML dashboard for V3.3 research.

No web server, no JS libraries, no network and no wallet. Reads JSON
ledger and health metrics and writes only the explicitly requested HTML.
"""
import argparse
import html
import json
from datetime import datetime,timezone
from pathlib import Path
from research_observer_health_v34 import metrics

CSS="""body{font-family:system-ui,-apple-system,sans-serif;background:#0e1218;color:#ecf2f6;margin:0;padding:26px;line-height:1.5}
main{max-width:1120px;margin:auto}header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:26px;margin:0}h2{font-size:18px;margin-top:30px}p,small{color:#abb6c3}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin-top:22px}
.card{background:#18212c;border:1px solid #344050;border-radius:12px;padding:16px}
.label{font-size:12px;color:#b6c5d5}.value{font-size:28px;font-weight:700;margin-top:5px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:11px;text-align:left;border-bottom:1px solid #334050}
th{color:#a7b6c5}.scroll{overflow-x:auto}.bad{color:#ff9c95}.good{color:#8bdcbc}
code{overflow-wrap:anywhere}.notice{padding:14px;border:1px solid #455266;border-radius:9px;margin-top:18px}
"""
def esc(value):
    return html.escape(str(value if value is not None else "—"),quote=True)

def render(ledger,health):
    if not isinstance(ledger,dict) or not isinstance(ledger.get("signals"),dict):
        ledger={"signals":{}}
    signals=ledger["signals"]
    rows=[]
    for key,item in list(signals.items())[-100:]:
        if not isinstance(item,dict):continue
        follow=item.get("followups") if isinstance(item.get("followups"),dict) else {}
        p5=follow.get("300") if isinstance(follow.get("300"),dict) else {}
        p15=follow.get("900") if isinstance(follow.get("900"),dict) else {}
        rows.append("<tr>"+''.join("<td>"+esc(x)+"</td>" for x in (
            item.get("mint"),item.get("strategy"),item.get("evidence_state"),
            item.get("security_status"),item.get("buy_quote_status"),
            p5.get("status","PENDING"),p5.get("quote_return_pct_before_network_fees"),
            p15.get("status","PENDING"),p15.get("quote_return_pct_before_network_fees")
        ))+"</tr>")
    stats=[
        ("Recorded signals",health.get("signal_count",0)),
        ("Research evidence passes",health.get("research_evidence_passed",0)),
        ("Sell route quotes",health.get("quote_samples",0)),
        ("Sell unavailable",health.get("sell_unavailable",0)),
        ("Missed horizon",health.get("horizon_missed",0)),
        ("Overdue followups",health.get("followups_overdue",0))]
    cards=''.join('<div class="card"><div class="label">'+esc(k)+
                  '</div><div class="value">'+esc(v)+'</div></div>' for k,v in stats)
    alerts=health.get("alerts",[])
    alert_markup="<p class='good'>No health alerts currently reported.</p>" if not alerts else (
        "<p class='bad'>"+esc(", ".join(alerts))+"</p>")
    reasons=health.get("entry_rejection_reason_counts",{})
    reason_lines="".join("<tr><td>"+esc(k)+"</td><td>"+esc(v)+"</td></tr>"
        for k,v in sorted(reasons.items(),key=lambda p:(-p[1],p[0]))[:12])
    coverage=health.get("followups_by_horizon_seconds",{})
    coverage_lines="".join("<tr><td>"+esc(h)+" seconds</td>"+''.join(
        "<td>"+esc(row.get(k,0))+"</td>" for k in
        ("quoted","unavailable","missed","pending"))+"</tr>"
        for h,row in sorted(coverage.items()))
    header=''.join("<th>"+esc(s)+"</th>" for s in
        ["Mint","Strategy","Research state","Mint check","Buy quote",
         "5m exit","5m quoted %","15m exit","15m quoted %"])
    status=esc(health.get("status","UNKNOWN"))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>GTA6 Radar — research dashboard</title><style>'+CSS+
        '</style></head><body><main><header><div><h1>GTA6 Radar — V3.4 Research</h1>'
        '<p>Quote-only research. No filled orders or realized P/L.</p></div>'
        '<div><small>Health</small><div class="value">'+status+'</div></div></header>'
        '<section class="cards">'+cards+'</section>'
        '<section class="notice"><b>Safety and data quality</b>'+alert_markup+
        '<small>Sample of 100 signals reached: '+esc(health.get("sample_sufficient",False))+
        '. Available-route return averages exclude missing sells and are not executable returns.</small></section>'
        '<h2>Sell-quote coverage by horizon</h2><div class="scroll"><table><thead><tr>'
        '<th>Horizon</th><th>Quoted</th><th>Unavailable</th><th>Missed</th><th>Pending</th>'
        '</tr></thead><tbody>'+coverage_lines+'</tbody></table></div>'
        '<h2>Top research-entry rejection reasons</h2><div class="scroll"><table><thead><tr>'
        '<th>Reason</th><th>Signals rejected</th></tr></thead><tbody>'
        +reason_lines+'</tbody></table></div>'
        '<p>Health OK means the observer is running, not that a token is safe to trade.</p>'
        '<h2>Most recently recorded signals (up to 100)</h2><div class="scroll">'
        '<table><thead><tr>'+header+'</tr></thead><tbody>'+''.join(rows)+
        '</tbody></table></div><p>Generated '+esc(datetime.now(timezone.utc).isoformat(timespec="seconds"))+
        '. Research-only, no trading approval.</p></main></body></html>')

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ledger",default="data/v33/research_ledger.json")
    p.add_argument("--report",default="data/v33/research_report.json")
    p.add_argument("--output",default="data/v33/research_dashboard.html")
    args=p.parse_args()
    out=Path(args.output).resolve()
    ledger_path=Path(args.ledger).resolve()
    report_path=Path(args.report).resolve()
    if out in (ledger_path,report_path):
        raise ValueError("HTML output cannot replace JSON source")
    try:ledger=json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError,ValueError):ledger=None
    try:report=json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError,ValueError):report=None
    health=metrics(ledger,report)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(render(ledger,health),encoding="utf-8")
    print(json.dumps({"html_report":str(out),"health":health["status"],
                       "signals":health["signal_count"],"alerts":health["alerts"]}))

if __name__=="__main__":main()
