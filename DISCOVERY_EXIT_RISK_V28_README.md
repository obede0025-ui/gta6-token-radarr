# GTA6 Discovery V2.8 — read-only exit-risk audit

One-command analysis of the completed V2.7 **8 first signal pools**.
No network requests, timers, orders, production edits, or new scans.

It reads both existing V2.7 databases **in SQLite read-only mode**:
- `discovery_v27_quotes.sqlite`: first quote and sell quotes at
  predetermined 60/300/900 second horizons.
- `discovery_v27_source.sqlite`: ONLY candidate attributes already
  recorded at signal time; no future signals or market observations
  are allowed in predictors.

The report:
- Counts one first signal per exact token and pool, regardless of
  FEED_ORDER/ACTIVITY_FIRST overlap.
- Compares **fixed** 1, 5 and 15-minute policies on the same signals:
  win/loss counts, coverage, mean, median, worst and best.
- Never selects the best exit per token after seeing prices.
- Separately reports missing sell routes rather than interpreting them
  as 0% price returns.
- Displays entry-to-immediate-resale route quote spread, and
  predeclared descriptive strata: buy share >=65% vs <65%, and
  liquidity >=$30k vs <$30k, measured at signal time.
- Reports worst fixed-15-minute cases for investigation.

**No TP/SL backtesting is possible with only 1, 5, 15m quotes**.
A stop loss between timestamps cannot be reconstructed accurately.
No Jupiter quote guarantees a fill; no network fees or on-chain failures
are simulated. Do NOT infer safety or profitable execution from 8 tokens.

## VPS single command

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_exit_risk_v28 -q && ~/gta6-source/.venv/bin/python research_discovery_exit_risk_v28.py --examples 5
```

One JSON report. No need to rerun V2.7 or delete any SQLite files.
The new V2.8 tests have not been executed on your VPS until this runs.
