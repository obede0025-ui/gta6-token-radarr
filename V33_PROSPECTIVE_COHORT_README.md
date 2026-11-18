# V3.3: prospective quote-only study and stronger token checks

## Implemented on the research branch (NOT production trading)
- `research_quote_cohort_v33.py` creates a separate state ledger (default
  `data/v33/research_ledger.json`) and report. Existing A/B/C/B2 paper
  portfolios and the learning database are opened only for reading.
- Tracks up to 3 **new** B/A/C candidates per run independently of
  already-full A/B/C paper position slots; retains historical traded
  lists and momentum thresholds.
- Entry only starts for a live observation <=120 seconds old.
- Stores **hypothetical Jupiter buy quote raw output**, NOT a trade fill.
- At 5 and 15 minute follow-ups, requests fresh indicative sell quotes
  for the SAME raw token quantity. Reports `HORIZON_MISSED` or
  `SELL_ROUTE_UNAVAILABLE` instead of inventing returns.
- Records *quote return before gas/fill/transfer costs*, not realized P/L.
- `research_preentry_probe_v31.py` now refuses to waste Jupiter/RPC
  requests on already-stale observations (critical for 5-minute scanner
  windows). RPC 429, timeout and invalid data remain blocked.
- The SPL mint screen rejects executable program accounts, invalid
  supply/decimals and corrupt authority data. Token-2022, holder
  concentration, LP control and executable sell confirmation remain
  unverified/blocking, as before.
- Tests run automatically in GitHub Actions on the research branch.
  The actual V3.3 ledger is **not yet scheduled** on VPS or production,
  to prevent unreviewed state changes.

## Local / VPS test before enabling observations

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_mint_integrity_v33 tests.test_research_quote_cohort_v33 tests.test_research_preentry_probe_v31 tests.test_research_preentry_evidence_v31 tests.test_radar_jupiter_pacing_v30 tests.test_research_risk_gate_v29 tests.test_research_preentry_fastdiag_v32 -q
```

## Optional V3.3 run — distinct research-only state

Before the first run confirm the two input paths exist and that
`data/v33/` does not contain a prior ledger from another experiment.
The command creates ONLY two new files in `data/v33/`. Existing
learning database, A/B/C and B2 portfolios stay unchanged.

```bash
cd ~/gta6-v32-gate-pilot && ~/gta6-source/.venv/bin/python research_quote_cohort_v33.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --portfolio /home/ubuntu/gta6-paper-vps/data/v591/portfolio.json --ledger /home/ubuntu/gta6-v32-gate-pilot/data/v33/research_ledger.json --report /home/ubuntu/gta6-v32-gate-pilot/data/v33/research_report.json
```

Repeated manually at around 5 and 15 minutes after each signal, it can
record valid horizon quotes; windows missed due to offline status or
rate limiting remain classified as missed. A scheduled VPS deployment
should only be considered after verifying the data retention, clock,
API quota and recovery behavior.

**No private keys, transaction endpoints, trading approvals, executions,
or modifications to existing paper engines.**
