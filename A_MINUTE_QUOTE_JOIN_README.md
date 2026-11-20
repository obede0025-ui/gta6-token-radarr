# Strategy A → minute observations → prospective Jupiter quote audit

This **read-only**, one-shot diagnostic:
1. Reconstructs historical **A-proxy** from three source snapshots at 3.5–20 minute spacing and the existing market criteria. There are no retrospective fills.
2. Deduplicates repeated observations of each exact mint/pair with a 15-minute research-only cooldown.
3. Finds the first DexScreener minute observation within **0–120 seconds after the A signal**. This mark is a delayed observation, not a signal-time executable entry.
4. Calculates +1/+3/+5 minute price, liquidity, volume and transaction buy-share changes **from that first available minute observation**. Missing times are not interpolated.
5. Optionally joins the isolated `data/focus/forward_quotes.json`, but ONLY if same exact mint, pair and prospective A signal time within 120 seconds. Actual quote followups retain their original anchor `first_quote_utc`, not the baseline minute observation or signal time.

Run on the VPS (NO orders, NO outbound API, NO writes):

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_a_minute_quote_join -q
~/gta6-source/.venv/bin/python research_a_minute_quote_join.py \
  --snapshots /home/ubuntu/gta6-paper-vps/data/learning.db \
  --minute-db /home/ubuntu/gta6-v32-gate-pilot/minute_market_pilot.sqlite \
  --cohort /home/ubuntu/gta6-v32-gate-pilot/data/focus/forward_quotes.json \
  --hours 24
```

The JSON output shows counts, unique pairs and five example records. When the
cohort is absent or has no exact A match, it correctly reports
`NO_MATCHED_PROSPECTIVE_COHORT`. **It does not create Jupiter evidence.**

Interpret 5-minute changes as observed marks, not realized profits or independently
executable returns. The minute market source has DexScreener poll timestamps:
`source_snapshot_at` is the candidate-selection snapshot and should not be
treated as the market quote's own timestamp.

The original Strategy A engine and risk filters are **not updated** by this
experiment. Evidence for a future improvement requires independent, prospective
samples and quote/fill-cost checks.
