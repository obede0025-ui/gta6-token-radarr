# Weighted momentum shadow experiment

Read-only audit over the isolated, existing minute-market pilot SQLite. No
external API requests, orders, paper portfolio changes or systemd modifications.

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_weighted_momentum_shadow -q
~/gta6-source/.venv/bin/python research_weighted_momentum_shadow.py \
  --db /home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite \
  --hours 168
```

For each mint, choose the earliest market-qualified observation across all
observed pairs (not the first winning opportunity). Require exact same-pair
minute observations at t+1, t+3, t+5 with a 60-second max lag.
Compare *changes between minute 1 and minute 3* in baseline-relative price and
liquidity percent changes, and in transaction buy-share percentage points.

Frozen exploratory methods:
- `strict_all_positive`: all 3 change indicators > 0.
- `balanced_fixed`: 40% price / 40% liquidity / 20% buy-share, normalized by 5
  percentage points / 5 percentage points / 2 percentage points respectively;
  normalized contributions clipped to [-2,+2], threshold >=0.35.
- `price_focused_fixed`: 60% price / 20% liquidity / 20% buy-share, same scales
  and threshold.

Separate precautionary shadow warning at t+3: liquidity below $20,000 or
dropped by >=80% from qualifying baseline. This warning is never overridden by
a momentum score. The observed label at t+5 comes from existing research
classifier; all quote availability is unverified in this data.

The script also calculates the observed price change from the t+3 decision
observation to the t+5 observation, not a purported return from an entry at
t0. No Jupiter quotes or fills are created or inferred.

**IMPORTANT:** The historic 7-day dataset was previously inspected and used
to form the hypothesis. These metrics are exploratory and NOT an
out-of-sample validation. Do not select/tune these score weights from the same
dataset and claim a proven edge. A locked prospective cohort, with actual
quote availability and fees/slippage recorded at the relevant times, is
needed before any trading changes.
