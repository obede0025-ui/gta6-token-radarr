# GTA6 Discovery V2.2 — Quality-First Prospective Shadow

This is **separate from and does not alter** V1, V2, minute market,
B3, M1, Jupiter, or frozen forward cohorts.

## A/B design

Both cohorts use the same DexScreener token-profile/latest-boost
candidate feeds, capped at 60 candidate token mints per manual run.
Each current cycle requests the complete candidate set (max 2 token
batch HTTP GETs, plus 2 discovery feed GETs), using a shared current
payload. Each arm gets **up to 20 slots** (change via `--slots`).

- **FEED_ORDER**: the first 20 in source feed order.
- **ACTIVITY_FIRST**: ranks the *same* current feed candidates using
  their last **previously stored** market sample: known positive price,
  pool age <=24h, liquidity >= $20k, 5m volume >= $250,
  buy count > sell count. If none meet all, prioritizes activity and
  finally feed order. Missing history defaults to feed order.

**No same-snapshot lookahead**: rankings are determined before fetching
current DexScreener token prices. Therefore the first run generally
selects the same tokens for both arms. Candidate quality is evaluated
on the contemporaneous market payload *after* selection.

The V2 age/liquidity/volume/pressure research base gates are
unchanged. Momentum separately requires two selected samples of the
same pair in the **same cohort**, 45–600 sec apart, with >=10%
increase in rolling m5 volume and current buys>sells. Unknown
liquidity or pool age fails closed.

`quality_market_samples` contains market snapshots for all
discoverable mints, while `quality_cohort_samples` and
`quality_signals` remain separated by cohort. No Jupiter quotes,
executable-liquidity checks, execution, trading or production timers.

## Run on VPS

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_quality_v22 -q && ~/gta6-source/.venv/bin/python research_discovery_quality_v22.py --slots 20
```

For a second prospective sample, invoke the same script at least
60 seconds later; after a third sample, compare outcomes using:

```bash
~/gta6-source/.venv/bin/python research_discovery_quality_v22.py --report-only
```

No timer automatically installed. Keep existing JSON and SQLite files.
The first cycle does not prove a better ranking. Do not declare
activity-first superior based on one or two cycles: selection changes,
distinct *new* base-qualifying mints and spaced-momentum signals should
be evaluated across many prospective runs.

Feed boosts are promotional and may be paid; they are not all Solana
launches. This method studies *monitoring priority* on one shared feed
universe, not faster token launch discovery or profitable fills.
