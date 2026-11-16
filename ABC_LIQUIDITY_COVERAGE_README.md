# GTA6 one-shot A/B/C liquidity-coverage shadow test

**No timer is installed, no production DB changes, no orders or trades.**

A: 30-token original minute selection from learning.db.
B: existing shadow v2 selection (up to 30), using historical missing-liquidity patterns to rotate low-value candidates.
A and B share a new contemporaneous DexScreener payload for their combined token set (up to 60 distinct mints, batched 30/request). Only the **pinned exact pair** counts. Compare known USD liquidity and preliminary base pass (>=20000 USD liquidity, >=250 m5 volume, buys>sells); **not** age/momentum or verified fills.

C: one independent GeckoTerminal **indexed on-chain-derived** public query for up to 10 pinned pools with missing DexScreener liquidity. This is *not direct Solana RPC accounting*. The returned `reserve_in_usd` is kept **separate**, not backfilled into DexScreener or B3 states. Pool IDs must match exactly; no alternate pair assumptions. This public API is cached and rate limited; run only manually initially. Source: https://api.geckoterminal.com/docs/index.html

## VPS commands

```bash
cd ~/gta6-v32-gate-pilot
git pull --ff-only origin feature/v32-preentry-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_minute_abc_coverage tests.test_research_minute_market_pilot tests.test_research_pair_liquidity_diagnostic -v
~/gta6-source/.venv/bin/python research_minute_abc_coverage.py --c-limit 10
```

Expected JSON: `A_current_selection`, `B_shadow_v2_selection`,
`B_minus_A`, `C_independent_pool_indexer`, and `api_calls`.
Do not act on a single snapshot. Do not enable a cron/systemd timer for
the C endpoint without first addressing rate limits, quota and prospective
cohort comparability. A positive indexed reserve is **not** confirmation
of a tradable Solana route.

No pre-existing frozen cohort is reset. Original selection and filters
remain unchanged.
