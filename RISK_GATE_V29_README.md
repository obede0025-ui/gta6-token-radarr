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

## Single command on VPS

The VPS checkout contains the V2.9 module only on
`feature/v32-preentry-gate-pilot`. The default paths in that checkout
may not include the GitHub main collector reports, so first retrieve
the collector reports separately if necessary. Better: run this against
a local checkout of `main` that includes `data/v594` and
`data/v612` JSON reports, or pass a root with those paths.

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_risk_gate_v29 -q && ~/gta6-source/.venv/bin/python research_risk_gate_v29.py --root .
```

If the report files are missing or stale, the report will explicitly
say BLOCKED. This is correct fail-closed behavior, not proof that
active servers have failed.

For a comprehensive CURRENT GitHub report, run the module where current
GitHub main collector reports exist, ensuring no production changes.
Review failed-rate-limit evidence before implementing globally shared
Jupiter rate limiting and capped backoff in a separate tested revision.
