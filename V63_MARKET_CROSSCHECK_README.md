# V6.3 market-vs-exit quote crosscheck (research only)

Runs **offline** against an existing Jupiter quote JSON and a local SQLite learning database. Does not alter the database, paper portfolios, scanner, or trades.

Flags: `LOW_OR_UNKNOWN_LIQUIDITY` (under $10,000, including zero); `PRICE_DISAGREEMENT` (Jupiter implied sale-price / contemporaneous Dexscreener price outside 0.8–1.2); `STALE_QUOTE`, `TIMESTAMP_MISMATCH`, `QUOTE_UNAVAILABLE`, `NO_SNAPSHOT`, and invalid values (classified UNKNOWN). All thresholds are **experimental audit rules**, not live-trade safety guarantees.

The market snapshot and quote timestamps must be within 15 minutes and quotes cannot be older than 15 minutes. Comparing quotes and snapshots made at different times is uncertain. Even a `PASS_RESEARCH_ONLY` does **not** prove a token's identity, that an exit will settle, or that funds are withdrawable. These controls do not check contract risks, token holder permissions, sell restrictions, pool identity, network fees or slippage over time.

## Offline unit tests
```bash
python3 -m unittest discover -s tests -p 'test_*_v63.py' -v
```

## Manual VPS crosscheck, on the isolated test clone
```bash
python3 market_crosscheck_v63.py --quotes v63_quotes.json \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --output v63_market_audit.json
```

Do not schedule in production or feed results to live trades.
