# Liquidity spike research classifier (read-only)

Observational research only, **not a trading gate**. Reads the isolated
`minute_market_pilot.sqlite` built by the running minute-market pilot.
No new scanner and no API calls.

Run:

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python research_liquidity_spike_classifier.py \
  --db /home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite \
  --hours 24
```

For every valid entry observation (liquidity >=20k, m5 volume >=250,
buys>sells, age 0–24h), compares the **same exact mint + pair** with the
first observation at/after +5 minutes (up to 60 seconds late). A valid
five-minute increase with closing liquidity under $20k or a fall of 80%+
is classified `MOMENTUM_LIQUIDITY_COLLAPSE` before any spike label.
Other jumps of 100%+ without a corresponding executable sale quote receive
`PRICE_SPIKE_UNVERIFIED`; increases >=20% with >=$20k liquidity receive
`MOMENTUM_WITH_LIQUIDITY`; small/negative movements are
`NO_QUALIFIED_MOMENTUM`.

`SELL_QUOTE_VERIFIED_RESEARCH` is a **separate optional evidence dimension**,
never proof of a filled trade and never a replacement for liquidity risk.
The isolated minute database contains **no Jupiter quotes**; therefore
its historical records ALWAYS carry
`SELL_QUOTE_UNAVAILABLE_OR_UNCHECKED`, with no fabricated confirmations.

`observed_at` is the minute pilot's observation time.
`source_snapshot_at` refers to earlier source DB selection, **not**
the refresh time for the DexScreener API response. Same-pair marks may be
observationally linked; quote executability, token security, and realized
profit are unknown.

Windows overlap and cannot be used as independent trade samples.
No threshold tuning or trading authorization should use these figures
without forward, out-of-sample evaluation.
