# Pair-liquidity root cause investigation (read-only)

## Why

Repeated `LIQUIDITY_UNKNOWN` rows are not proof that a token lacks liquidity.
The existing market pilot selects an exact pinned pair from `learning.db` and
calls DexScreener token endpoint, but accepts only that exact pair. An API
response may show the same token on a different pool, or omit `liquidity.usd`
for the pinned pair. Never copy alternative-pair liquidity into the pinned
pair: price / exit / route can differ.

Also note: `minute_observations.source_snapshot_at` is the original
`learning.db.snapshots.scanned_at` selection timestamp. It is NOT the
DexScreener HTTP response time. `observed_at` is the pilot polling time.

## Install and run

```bash
cd ~/gta6-v32-gate-pilot
git pull --ff-only origin feature/v32-preentry-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_pair_liquidity_diagnostic tests.test_research_minute_market_pilot -v
~/gta6-source/.venv/bin/python research_pair_liquidity_diagnostic.py --hours 6 --limit 20
```

The diagnostic performs **one live DexScreener GET**, with up to 20 unique
mints selected from recent high-repeat `LIQUIDITY_UNKNOWN` pairs. It records
no new state and modifies no SQLite file. Run sparingly due to external API
rate limits.

Interpret classifications:
- `EXACT_LIQUIDITY_NOT_REPORTED`: price exists but exact pinned pair has no USD liquidity field.
- `PINNED_PAIR_NOT_RETURNED`: mint returned, pinned pair absent.
- `TOKEN_NOT_RETURNED`: API response had no matching base-token mint.
- `EXACT_LIQUIDITY_NOW_REPORTED`: exact pair now has data; earlier missing rows may reflect transient states.
- `EXACT_PRICE_NOT_AVAILABLE`: exact pair returned with missing/invalid USD price.

`with_viable_alternative_pair` counts pairs with usable current prices and
nonnegative known USD liquidity, not approved trades. An alternative pair
is only a research candidate. Do not alter B3 filters or production pair
selection based solely on this initial snapshot.

Next: if results show genuine missing exact pairs, implement a separate,
prospective pinned-pair versus alternative-pair **shadow** collector and
measure candidate eligibility/outcomes, rather than silently overriding
historic pair IDs or original collector state.
