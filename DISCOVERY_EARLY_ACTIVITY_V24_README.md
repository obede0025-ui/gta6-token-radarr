# GTA6 Discovery V2.4 — early-activity audit

**One-command, offline, read-only research.** Uses existing V2.2
`discovery_quality_v22.sqlite` sampled data. No timer, API calls, orders,
database writes, production scanner/B3/M1/Jupiter changes or cohort resets.

V2.3 showed five base-qualified observations per cohort where rolling
5-minute volume did not increase. V2.4 asks whether **earlier changes in
rolling 5-minute buy counts**, alongside volume, suggest a separate
*watch-only* research category.

For each exact mint+pair+cohort, use up to three stored snapshots.
Current base gate must already pass (price, known liquidity >=$20k,
5m volume >=$250, buys>sells and pool age <=24h).
For a provisional watch-only result, require:
- Previous selected exact-pair sample 45–600 seconds earlier.
- Current rolling 5m buy share >=65%.
- Rolling 5m buy count >=15% higher than prior sample.
- Rolling 5m USD volume no more than 15% lower than prior sample.
- A three-sample persistence flag additionally requires three spaced
  observations with base-pass at all three snapshots.

**These are diagnostic hypotheses, NOT approved signals.** Overlapping
five-minute windows mean count changes do not represent *new* buy
transactions or independent acceleration. The audit does not test
returns, fills or quote execution and is retrospective; future
prospective validation is mandatory before considering selection
changes.

## VPS one-liner

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_early_activity_v24 -q && ~/gta6-source/.venv/bin/python research_discovery_early_activity_v24.py --examples 4
```

One JSON report is returned, with watch-only cases and overlapping
exclusion reasons, comparing the two existing V2.2 cohorts. No need to
copy many intermediate command outputs.

If no early-watch pairs appear, this does not mean the tests failed;
the historical window may simply have no matching cases.
