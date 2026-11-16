# GTA6 Discovery V2.6 — prospective 1/5/15-minute outcomes

This experiment **cannot recreate** the 1/5/15 minute price outcomes of
the four V2.5 signals from an earlier session: their scheduled times
have passed. It collects **new** signals and measurements prospectively.

## Protocol

- Standalone trial (foreground): **25 attempts spaced 60 seconds**.
- In the first **10** cycles, run the existing V2.2 quality selection
  with its unchanged FEED_ORDER and ACTIVITY_FIRST cohorts, using
  *fresh dedicated* `discovery_v26_source.sqlite`.
- Freeze new momentum-proxy signals detected in this run, their exact
  token mint + pool address, timestamp, and signal-time USD market price.
- Monitor only the exact pool for **1, 5, 15 minutes** following each
  signal, allowing up to 90 seconds after the target for the observed
  snapshot. Missing/stale observations are recorded as unavailable and
  are never backfilled from an arbitrarily later price.
- Source signals and measured outcomes are stored in distinct
  `discovery_v26_source.sqlite` and `discovery_v26_outcomes.sqlite`;
  B3/M1, V2.5 state, production data and timers untouched.
- Default fixed cost assumption: **2 percentage points round-trip**,
  so `net_hypothetical_pct_after_cost` is the observed market-price
  change less that assumption. This is **not a swap quote**, real
  slippage, an executable sell, an audited cost model or a profitable
  trading simulation.
- Exit price and pool liquidity are public DexScreener market API
  observations. The presence of liquidity does not prove a token can
  actually be sold.
- No actual orders, wallets, API trades, production scanner approvals
  or permanent services. Stops after three consecutive errors.

## One command to execute on the VPS

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_outcomes_v26 -q && ~/gta6-source/.venv/bin/python research_discovery_outcomes_v26.py
```

The trial runs in foreground for **about 24 minutes**, and prints one
JSON report when finished. Keep the terminal open. To review an already
written report without starting a new run:

```bash
~/gta6-source/.venv/bin/python research_discovery_outcomes_v26.py --report-only
```

Do not delete old databases to rerun. The script intentionally refuses
to overwrite already existing trial databases. A separate experiment
requires a new filename for both `--source` and `--outcomes` options.
If there are zero new signals, results must state `NO_NEW_SIGNALS`
rather than implying zero returns.
