# Chronological market simulation — research only

The historical seven-day minute dataset has already been inspected and used
to propose the hypotheses. This is therefore an **exploratory pseudo-holdout**
and cannot establish a working or profitable strategy.

This simulator uses the existing read-only minute DB and frozen candidate
methods. It selects **only the earliest market-eligible observation for each
token**, including losing tokens, then requires market marks after +1/+3/+5
minutes. It models a decision **at +3 minutes**, not at the start.

Candidate models: strict all-positive and the fixed 40/40/20 or 60/20/20
normalized scores at thresholds 0, 0.35, and 0.7. All models apply the
separate +3min liquidity warning. Chronological 60/20/20 train/validation/
pseudo-test partitions, with a 20-minute embargo at each boundary, ensure
future pseudo-test records do not choose weights. There is no repeated
automatic threshold search on the final split.

Stress scenario for **hypothetical decision-to-5-minute mark**:
4%, 8%, or 12% assumed aggregate friction subtracted from price movement;
if the output is a liquidity-collapse class or has below-$20k liquidity,
assume a worst-case -100% loss. Actual exit fill outcomes are UNKNOWN. These
costs are sensitivity scenarios, not measured Solana/Jupiter execution fees.
No entry/exit quotes or real trades are created.

Run:
```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_chronological_market_simulator -q
~/gta6-source/.venv/bin/python research_chronological_market_simulator.py \
  --db /home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite \
  --hours 168
```

Selection uses training records only. Validation acceptance gates are fixed:
at least 10 validation PASS, positive stressed mean, >50% positive stressed
returns and no liquidity collapse among PASS. Even if validation passes,
no claim of profitability is permitted, because there are no executable
quotes/fills and the historical data were previously investigated.

To establish a real edge, freeze the selected rule and run the existing
prospective quote-only research on **future signals**, including failed routes,
slippage, liquidity, security and transaction fees. Do not deploy trading.
