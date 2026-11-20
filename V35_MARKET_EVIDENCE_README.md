# GTA6 V3.5 isolated market-evidence monitor

V3.5 is an **optional manual** quote-only market evidence sample for existing V3.3 research signals. It does **not** launch/stop systemd workers, modify V3.4, or execute trades.

## What it does
- Reads existing V3.3 `research_ledger.json` (no writes).
- Reads SQLite `snapshots` via SQLite `mode=ro` and `query_only=ON`.
- Picks up to 3 distinct ledger mints with a matching database snapshot no more than **120 seconds** old; older tokens are excluded, not deemed safe.
- Uses read-only Solana RPC mint account retrieval and Jupiter GET-only swap quote (hypothetical historic token amount, no buy).
- Re-evaluates freshness after network requests and computes fail-closed Token-2022 evidence points.
- Emits the report to stdout and optionally to a **separate** JSON file, e.g. `data/v35/market_evidence.json`.

## VPS smoke test (manual only)

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_v35_market_evidence -v
~/gta6-source/.venv/bin/python research_v35_market_evidence.py \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --ledger /home/ubuntu/gta6-v32-gate-pilot/data/v33/research_ledger.json \
  --output /home/ubuntu/gta6-v32-gate-pilot/data/v35/market_evidence.json \
  --limit 3
```

There is **no auto-enable timer** in V3.5. The manual trial is intentionally independent of V3.4, and external API quota is limited by 3 candidates and existing Jupiter pacing. Jupiter pacing only applies within this Python process; other running jobs may share external rate limits.

## Interpretation
- `selection.fresh_mints=0`: no eligible fresh market snapshots at the moment; **not** an operational error.
- `liquidity: PASS`: snapshot liquidity >= $20,000 at the observation time, not proof of locked LP.
- `sell_route: PASS`: Jupiter returned a positive GET-only quote for a hypothetical historical quantity. It is not a fill or proof tokens are transferable.
- `UNKNOWN`: missing, stale, invalid, unavailable or unverified evidence; not equivalent to zero risk.
- All paper/live approvals stay `BLOCK`. No retrospective edits of V3.3 research ledger, portfolio, source database or V3.4 output. No claim of profitability.

Note: Jupiter quote output can depend on aggregator routing and can use a pool other than the original detected pool. The monitor does not validate holder concentration or LP lock and must not be used to turn on execution.
