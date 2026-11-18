# V3.4 — isolated research observer and risk dashboard

## Implemented (research branch only)

- `research_quote_cohort_v33.py`: separate prospective V3.3 signal ledger,
  with indicative 5 and 15 minute exit quotes, no simulated fills.
- `research_observer_health_v34.py`: validates source report freshness,
  missed/unavailable sell routes, overdue follow-up windows and alerts.
- `research_dashboard_v34.py`: safe standalone local HTML read-only dashboard
  showing token-by-token research states (escapes user/token strings).
- `research_v34_cycle.py`: one-command research cycle: updates **only**
  `data/v33/research_ledger.json`, `research_report.json`,
  `research_health.json` and `research_dashboard.html`.
- `deploy/systemd/gta6-v34-research.service` and `.timer`: optional
  isolated observer service. **Files are NOT installed or enabled** on the
  VPS by GitHub commits. Production A/B/C/B2/GitHub collectors unchanged.
- Risk tests execute automatically on research-branch pushes and can
  perform a separate ephemeral read-only snapshot inspection of main.

## One command to validate local code on VPS, without enabling services

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_v34_service tests.test_research_v34_cycle tests.test_research_dashboard_v34 tests.test_research_observer_health_v34 tests.test_research_mint_integrity_v33 tests.test_research_quote_cohort_v33 tests.test_research_preentry_fastdiag_v32 tests.test_research_preentry_probe_v31 tests.test_research_preentry_evidence_v31 tests.test_radar_jupiter_pacing_v30 tests.test_research_risk_gate_v29 -q
```

## One manual observation cycle (writes only separate research state)

```bash
cd ~/gta6-v32-gate-pilot && ~/gta6-source/.venv/bin/python research_v34_cycle.py
```

Open the generated local HTML using a browser or copy it to your desktop.
**Do not expose the dashboard or private VPS folders over a public server
without access controls.**

## Optional deploy of observation-only systemd timer

This should be considered only after the VPS tests and a manual research
cycle pass and the collector DB path is verified. Requires the VPS user's
explicit action (sudo) and requires no wallet, swap keys or trading credentials.

```bash
cd ~/gta6-v32-gate-pilot && \
sudo install -m 0644 deploy/systemd/gta6-v34-research.service /etc/systemd/system/gta6-v34-research.service && \
sudo install -m 0644 deploy/systemd/gta6-v34-research.timer /etc/systemd/system/gta6-v34-research.timer && \
sudo systemctl daemon-reload && \
sudo systemctl enable --now gta6-v34-research.timer && \
sudo systemctl status gta6-v34-research.timer --no-pager
```

Stop safely at any point:

```bash
sudo systemctl disable --now gta6-v34-research.timer
```

Inspect run errors and quote limits:

```bash
sudo journalctl -u gta6-v34-research.service -n 80 --no-pager
```

The dedicated timer starts after boot and schedules one cycle after the
previous cycle finishes; the service enforces 90 second per-run timeout.
Times may be missed if RPC/Jupiter throttles or 5m source scans arrive
late. Missing follow-ups are **not** converted into synthetic wins.

## Still not solved by code alone

Actual trade settlement, holder distribution and liquidity pool lock
verification; empirically validated net profitable outcomes with adequate
prospective sample size; guaranteed execution during extreme volatility.
These require additional independent evidence and integration testing.
No readiness/profitability percentage is proven by unit test counts.
