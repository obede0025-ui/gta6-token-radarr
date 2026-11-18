# GTA6 Risk Gate V2.9 — offline, fail-closed

Read-only audit of the **latest current** GitHub collector reports.
Reassesses old open paper positions against conditions that must NOT be
misrepresented as tradable:
- Market snapshot older than **12 minutes**, missing, or timestamp in future
- Market liquidity unknown or below **$20,000**
- Paper position older than **4 hours** (research horizon, not automatic liquidation)
- Missing, stale, or malformed monitoring reports (>15 min)
- No contemporaneous sell quote / HTTP 429
- Missing token-security evidence
- Execution-accuracy quote unavailable

No rule *ever* approves live trading. Results explicitly say the audit is
**not integrated** into active B3/M1 or Github A/B/C paper engines. This
is an informational fail-closed **research report** only. It never silently
closes existing simulated positions, changes live permissions, network
requests, databases or existing JSON files.

## Single VPS command for current GitHub collector reports

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && git fetch origin main && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_risk_gate_v29 -q && ~/gta6-source/.venv/bin/python research_risk_gate_v29.py --git-main
```

Reads the four latest published reports directly from git object `origin/main`
without modifying the active main checkout. The data may still be stale
depending on GitHub workflow freshness; stale inputs fail closed.


This audit is **diagnostic only**. A separate tested change is needed before any operational paper engine uses these findings. No automated liquidation or live trading approval.
