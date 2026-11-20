# First 1 / 3 / 5 minutes — Strategy A forward audit

Reads the isolated `data/focus/forward_quotes.json` prospective cohort, matched
to SQLite snapshots by **exact mint + pair**. Never reads future data to
construct a baseline and never treats a historical paper mark as a Jupiter sale.

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python research_a_first_minutes.py \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --cohort /home/ubuntu/gta6-v32-gate-pilot/data/focus/forward_quotes.json
```

The cohort is created **only** by explicitly running
`research_focus_forward_quotes.py` (manual, no timer; refer to
`FOCUS_A_B2_RESEARCH_README.md`). If the cohort does not exist, the
report refuses to invent data.

Market checkpoints are **+60, +180, +300 seconds relative to the initial
signal timestamp**. The earliest snapshot at/after the target and within
60 seconds counts as observed; otherwise data is missing or not due. Market
variables: price, liquidity, volume (last 5m) and buy transaction share.
These may be mutually dependent, especially liquidity and price.

Separate quote checkpoints are **+60, +300, +900 seconds relative to
the first Jupiter hypothetical quote**. Quote-only figures are not fills
or portfolio P/L, and MUST NOT be conflated with the market checkpoint
timestamps. Missing/stale measurements do not become successes.

No changes to timers, thresholds, portfolio, scanner, existing V3.4
cohort, source DB, real trades or paper execution.
