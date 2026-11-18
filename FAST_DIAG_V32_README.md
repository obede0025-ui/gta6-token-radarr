# GTA6 V3.2 — one-command risk / discovery diagnosis

V3.1 found 0 recent candidates. V3.2 explains the difference between:
- scanner data older than 12 minutes,
- no snapshots in the past 12 minutes,
- market measurements failing liquidity >=$20k, volume >=$250 and buys > sells,
- momentum or paper-portfolio filters rejecting recent market survivors,
- actual recent candidate(s) present.

It does NOT relax any strategy thresholds or initiate orders. Inputs are
opened with SQLite `mode=ro` and `query_only=ON`; it does not write files
or call network APIs.

## One command for updating, testing and diagnosing

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_preentry_fastdiag_v32 tests.test_research_preentry_evidence_v31 tests.test_research_preentry_probe_v31 tests.test_radar_jupiter_pacing_v30 tests.test_research_risk_gate_v29 -q && ~/gta6-source/.venv/bin/python research_preentry_fastdiag_v32.py
```

No VPS services, active GitHub workflow or portfolio state changed.
A command that prints `selected_candidates: 0` is not a technical error.
This diagnostic helps identify the actual constraint before investing in
higher-frequency discovery or adjusting paper rules.

Possible future improvements include increasing observations by safely
scheduling a separate scout, maintaining an index for fast recent SQLite
lookups (only after backup and query plan review), and grouping tests into
GitHub workflow steps. None is implemented here.
