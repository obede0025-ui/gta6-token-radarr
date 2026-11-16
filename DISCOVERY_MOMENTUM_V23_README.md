# Discovery V2.3 — one-command read-only momentum diagnosis

Reads the existing `discovery_quality_v22.sqlite` and diagnoses the most recent
three recorded V2.2 cycles without creating new observations, HTTP requests,
orders or altered state.

- Per current candidate: exact mint/pair, cohort, liquidity, 5m USD volume,
  previous same-cohort/same-pair volume, percent change, buys/sells, and
  previous-sample gap.
- Lists overlapping base blockers (known liquidity below $20k, missing
  liquidity, volume below $250, weak buys, pool age, missing data).
- For candidates that satisfy all base criteria, explains why no momentum
  was confirmed: no prior selected pair sample, missing comparable volume,
  <45 sec gap, >600 sec stale gap, <10% rolling 5m volume growth, or weak
  buy pressure.
- Does not interpret rolling 5m volume growth as tradeable momentum.
- Existing B3/M1 and V2.2 pipelines remain untouched.

Run on VPS:

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_momentum_v23 -q && ~/gta6-source/.venv/bin/python research_discovery_momentum_v23.py --cycles 3 --examples 6
```

Paste one report. For a shorter report, use `--examples 2`.
No timer is installed by this commit.
