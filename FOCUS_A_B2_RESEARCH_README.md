# GTA6 Focus — A versus B2, historical data, prospective follow-up

This is the only new research workflow for the next phase. It deliberately avoids introducing new trading strategies, relaxing risk thresholds, changing systemd services or executing trades.

## 1. Historical and comparable diagnostics (offline, no network)
From the VPS research checkout:

```bash
~/gta6-source/.venv/bin/python research_focus_a_b2.py \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --abc /home/ubuntu/gta6-paper-vps/data/v591/portfolio.json \
  --b2 /home/ubuntu/gta6-paper-vps/data/v611/b2_portfolio.json \
  --hours 720
```

Use --hours 720 for up to 30 days of available history. This reports:
- Closed A and B2 simulated P/L, median, counts and wins; open $25 stake exposures and total-loss stress outcomes.
- How many token mints appear in both groups. This is **not** a randomized paired profit comparison. Closed profits alone cannot prove a winning strategy.
- Historical snapshot gaps and overlapping market rejection counts (not independent tokens).
- Fresh signals based on existing A / B momentum criteria with strictly recent (<120s) market observations. **B2_SIGNAL_PROXY** is original B momentum only; it is **not** a full B2 quote/security approval.
- The signal shortlist never makes HTTP requests or changes a portfolio.

## 2. Prospective only; manual $10 hypothetical quote study
Run manually only if the historical report and operational checks are satisfactory:

```bash
~/gta6-source/.venv/bin/python research_focus_forward_quotes.py \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --state /home/ubuntu/gta6-v32-gate-pilot/data/focus/forward_quotes.json
```

When re-run, the study:
- Accepts at most three fresh mints that meet >=$20k reported liquidity, >=$250 volume / five min, positive buy pressure, and 0–24h pair age.
- Makes keyless Jupiter quote-only **GET** requests for a hypothetical 10 USDC -> token buy and immediate token -> USDC reversal. It does **not** acquire tokens.
- Uses the original, hypothetical token quantity to request indicative sell quotes at +60s, +300s and +900s. In the manual pilot, the operator must actually call the script around those times, otherwise the horizon is marked `MISSED_HORIZON`.
- A missing entry buy quote, stale snapshot, late follow-up or unavailable quote is never counted as a win.
- Keeps a separate isolated JSON cohort plus a `.lock` file under `data/focus`; capped at 200 distinct mint/pair signals. Does not write to the source database, existing V3.3/V3.4 ledgers, A/B/C or B2 paper portfolios.

There is **no systemd deployment or automatic timer** in this change. Multiple background workers may share Jupiter rate limits. The quote pacing limit is per process only, not global. Delays can cause the 1m window to be missed.

## Next research decision
Do not tune parameters or start live trading from closed-only P/L. Compare prospective A and B2 signal cohorts from equal timestamps and overlapping mint/pairs, record both availability and cost of selling, measure net quote-only returns after conservative fees, and separately audit unmatched signals and incomplete outcomes. At least a meaningful sample with out-of-sample checks is needed before declaring one approach superior.

All trade approvals remain `BLOCK`. Historical quotes and snapshots are not executed fills, and no holder concentration / LP-lock audit is implied.
