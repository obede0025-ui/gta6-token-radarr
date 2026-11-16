# GTA6 Discovery Shadow V1 — 30 vs 100 token research

Isolated **manual-only** prospective experiment. It does **not** replace the
production B3 selection, M1 criteria, current scanner, API timers or any original
database. It creates/updates only
`/home/ubuntu/gta6-v32-gate-pilot/discovery_shadow_v1.sqlite`.

## What changed

- Reads `/home/ubuntu/gta6-paper-vps/data/learning.db` in SQLite **read-only**
  mode using the *existing* `source_candidates()` ordering.
- Baseline = first 30 selected token/pool pairs. Expanded = first 100
  candidates with the same selection logic. This measures the marginal coverage
  of breadth; **it does not yet implement new early-source discovery**.
- Requests at most 100 distinct token addresses from the DexScreener Solana
  endpoint, batched at <=30 mints/GET (max 4 GET per manual cycle).
- Requires an exact pair, positive USD price, known USD liquidity >= $20,000,
  5m volume >= $250 and 5m buys > sells for *research base-pass*.
- Research-only 'volume momentum' proxy compares two spaced observations of
  the same exact mint+pair: 45–600 seconds apart, >=10% increase in rolling
  m5 USD volume, and current 5m buys > sells. This is **not** equivalent to
  B3's complete real momentum filter and does not prove profitability.
- Stores isolated sample rows with `PRIMARY KEY(observed_at,mint,pair)`;
  unique shadow signals by `PRIMARY KEY(mint,pair)`, without copying any
  existing production candidate signals or resetting any frozen research set.
- `--report-only` reads the isolated DB in read-only mode.

## Run on VPS

```bash
cd ~/gta6-v32-gate-pilot
git pull --ff-only origin feature/v32-preentry-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_shadow_v1 tests.test_research_minute_market_pilot -v
~/gta6-source/.venv/bin/python research_discovery_shadow_v1.py --limit 100
```

Run again after at least **60 seconds** to supply a second observation:

```bash
~/gta6-source/.venv/bin/python research_discovery_shadow_v1.py --limit 100
~/gta6-source/.venv/bin/python research_discovery_shadow_v1.py --report-only
```

If candidate availability is less than 100, the report will include fewer.
A first observation has zero momentum signals by design. A second run can have
none if the rolling m5 volume is flat or falling. Avoid treating this as a
broken scanner. No systemd timer is added or enabled. No original DB/production
timer/state is touched.

## Decision criteria (not trading rules)

Review whether the 31–100 extras yield prospectively *unique* additional
research base-passes and momentum signals, not just more stale or low-liquidity
observations. Evaluate source candidate age, API response time and quote
feasibility separately before any production change.

There is **no** automatic B3→M1 integration in this module, because B3
only admits candidates under its own more complete safety criteria.
