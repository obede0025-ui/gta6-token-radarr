# B3 Forward Outcome Audit — read only

Builds a separate JSON report from existing B3 shadow events and recorded Dexscreener market snapshots; measures changes after **15 and 60 minutes**, using only same-mint **and same-pool** snapshots made between the target horizon and 10 minutes after it.

Never treats missing prices as 0, never looks backwards from a target horizon. Pending horizons remain PENDING.

These are *indicative observed price changes*, not executable sell quotes, paper-trade PnL or actual investment returns; no claim that the market was tradeable. Sample selection may be severely biased. Retained events are capped at 10k upstream.

```bash
python3 -m unittest discover -s tests -p 'test_b3_forward_outcomes.py' -v
python3 b3_forward_outcomes.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --state /home/ubuntu/gta6-v63-test/b3_state.json --output /home/ubuntu/gta6-v63-test/b3_forward_report.json
```

No scheduling or production integration yet; validate on VPS first.
