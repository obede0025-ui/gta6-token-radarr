# GTA6 Discovery Shadow V2 — independent Solana discovery (manual research)

V1 widened the original learning.db shortlist from 30 to 100 tokens but
produced no additional preliminary passes in two observed cycles. V2
instead uses **independent public DexScreener latest-token-profile and
latest-boost feeds** for prospective token ideas. These endpoints are
visibility/promotion feeds, **not unbiased or comprehensive lists of new
on-chain launches**. Boosts can be purchased. The 30-token original
learning.db shortlist remains a contemporaneous research reference.

API documentation: https://docs.dexscreener.com/api/reference
- `/token-profiles/latest/v1`: latest token profile entries.
- `/token-boosts/latest/v1`: recently boosted tokens.
- `/tokens/v1/solana/{tokenAddresses}`: up to 30 token IDs per request.

## Safeguards

- Isolated database: `/home/ubuntu/gta6-v32-gate-pilot/discovery_shadow_v2.sqlite`.
- Original `learning.db` opened with SQLite `mode=ro`.
- No old database/table overwrite, no production states, no order execution,
  no Jupiter quotes, no timer or systemd unit.
- Token must be Solana; pairs must match actual token base mint.
- For independent discovery, choose the returned pair with usable price
  and highest **reported** USD liquidity; pair changes reset comparison.
- Reference group uses its **original exact pinned pair**.
- Research base-pass requires positive price, known liquidity >= $20k,
  5m volume >= $250, buys>sells, known pool age <=24 hours.
- Research momentum additionally requires 45–600 seconds between exact-pair
  samples, >=10% increase in rolling 5m USD volume, and buys>sells.
- Observations and signals deduplicated in the isolated DB.
- No signal is an entry approval: original B3 safety, exit quotes, actual
  slippage, mint safety and market manipulation remain untested.
- First observation cannot produce a momentum pass.

## VPS manual validation

```bash
cd ~/gta6-v32-gate-pilot
git pull --ff-only origin feature/v32-preentry-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_shadow_v2 tests.test_research_discovery_shadow_v1 -v
~/gta6-source/.venv/bin/python research_discovery_shadow_v2.py --max-discovered 60
```

Run the same command again at least **60 seconds later**, then:

```bash
~/gta6-source/.venv/bin/python research_discovery_shadow_v2.py --report-only
```

Calls per manual cycle: 1 latest profiles GET, 1 latest boosts GET,
at most 3 token-price GETs (30 baseline + 60 independent feed mints).
Respect API service quotas; do not enable a timer until prospective
coverage, throttling, and snapshot selection have been evaluated.

## Decision

Compare **new mints not already in baseline**, and more importantly **new
unique confirmed base-pass and consecutive-momentum candidates**, over
many prospective cycles. Age is based on `pairCreatedAt`, not mint
deployment. Higher coverage alone does not demonstrate improved trading
performance or discovery latency.

Current V2 uses *DexScreener's discovery/promotion feeds* independent of
our existing DB, but not independent of DexScreener as market-data provider.
For true pre-indexing discovery, a separate on-chain event source would be
needed and independently validated.
