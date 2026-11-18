# GTA6 V3.0 — Jupiter quote resilience (research branch only)

Changes the common `radar_quote_monitor.quote` quote-only GET function used by:
- `radar_quote_monitor.py` (roundtrip and paper-position sell quotes).
- `radar_execution_accuracy.py` (fresh B/B2 paper-position exit quotes).

New `radar_jupiter_pacing_v30.py` implements:
- 2.5 seconds minimum between requests **within one process**.
- Maximum three GET attempts.
- Retry ONLY connection timeout, connection errors, HTTP 429/500/502/503/504.
- Reads HTTP `Retry-After` seconds or date, capped at 12 seconds per retry.
- Other HTTP errors (for example HTTP 400) fail closed immediately.
- Missing RPC token decimals explicitly marked `DECIMALS_UNAVAILABLE`.
- No `taker`, wallet, signing or execution endpoints: quote only.

**Important limits:** `radar_quote_monitor.py` and
`radar_execution_accuracy.py` are separate Python processes and therefore
do NOT share a global cross-process quota. Other Jupiter users may still
trigger HTTP 429. Rate-limited/missing sell quotes remain unavailable,
NEVER assigned zero risk and NEVER interpreted as sellability.
The current GitHub Actions workflow and production VPS services are
unchanged until the tests pass and a separately reviewed rollout occurs.

## VPS validation — single command

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_radar_jupiter_pacing_v30 tests.test_research_risk_gate_v29 -q && ~/gta6-source/.venv/bin/python -m py_compile radar_quote_monitor.py radar_execution_accuracy.py radar_jupiter_pacing_v30.py
```

This performs **only offline unit tests and Python compilation**, no API
queries, no changes to portfolios, databases or active main workflows.
Return the output before integrating it into main.
