# M1 V3.2 — forward-only Jupiter entry/exit quote tracking

This is a **manual pilot**, not a systemd service or executable trading strategy.

- On first run it **bootstraps** from the current M1 V3 state and ignores all existing signals. No Jupiter call.
- On future runs, only NEW M1 V3 `WATCHING` or `OBSERVED_PRICE_TRIGGER` candidates enter this study.
- Entry quote: 25 USDC to token through Jupiter Swap V2 quote-only (no `taker` and no transactions). Reject entry if more than 360 seconds since B3 signal.
- Record the quoted atomic token quantity from that entry; later exit quotes price that **same quantity** token -> USDC, until 15 minutes since original B3 signal.
- Max 2 active hypothetical positions, max 4 Jupiter requests per execution, min 60 seconds between token sell quotes. Use separate file lock and state.
- If an entry route is unavailable, record the failure; do **not** invent a position. If an exit route is unavailable, record it.
- Quote return percentages are indicative and **not net realized PnL**: no executions, order slippage, inclusion failure, priority/network fees or account creation costs. Token balances can differ from quote after real buys.
- V3.2 does NOT modify original M1 V3, B3, the paper portfolio or any existing timer. No service is installed.

```bash
cd ~/gta6-v63-test
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_v3_2_forward_quotes.py' -v

# First manual run only initializes state and does NOT require the API key:
~/gta6-source/.venv/bin/python m1_v3_2_forward_quotes.py \
  --m1-state /home/ubuntu/gta6-v63-test/m1_v3_state.json \
  --state /home/ubuntu/gta6-v63-test/m1_v3_2_state.json
```

Future manual runs can source the existing private `/home/ubuntu/.config/gta6-radar/jupiter.env` locally. Never paste its contents into chat or commit secrets to GitHub. This prototype requires a manual run while candidates are fresh. **Do not infer continuous monitoring from one run**. Any eventual scheduling requires a separate review of API quotas, monitoring, fresh-signal detection and robustness.

Research note: the six-minute upper bound compensates for B3 discovery and service scheduling delays, but **does not extend the 15-minute total signal window**. A delayed entry is still a current quote, never a retrospective fill; mark entry lag in reports. Existing SKIPPED_STALE records are not retroactively replayed.
