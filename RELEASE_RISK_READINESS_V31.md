# V3.1 — risk-first, quote-only pre-entry research

## What is now implemented

**Production main: V3.0 Jupiter quote retry/pacing** was rolled out
separately on commit `1efe6ab`. GitHub Actions now checks unit tests
before collecting. This includes 2.5 sec intra-process quote pacing,
at most 3 attempts per transient failure, and capped Retry-After backoff.
Separate GitHub Actions jobs / machines still do not share a rate limiter.

**Research branch: V3.1 independent evidence gate**:
- `research_preentry_evidence_v31.py` is a pure fail-closed classifier.
- `research_preentry_probe_v31.py` collects strictly bounded candidate
  evidence with Jupiter **GET only** and Solana read-only `getAccountInfo`.
- Entry requires market observations <=120 seconds old, liquidity >=$20k,
  m5 volume >=$250, buys > sells, pair age <=24h, same mint/pair binding,
  buy+immediate reversal routes with <=3% quote loss, a <=120s-old
  mint-account check, disabled mint/freeze authorities, a supported token
  program and no flagged extensions.
- **PASS_RESEARCH_ONLY never means live or paper approval**. The gate
  reports evidence completeness only, not real fill safety. Missing
  evidence, network errors or HTTP 429 fail closed.
- The probe examines <=5 candidates and writes **nothing** to any DB or
  portfolio. A wallet or private key is never loaded. The RPC uses POST
  only for read-only JSON-RPC `getAccountInfo`, never transactions.

## Validation command — only unit tests, no APIs or trades

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_preentry_evidence_v31 tests.test_research_preentry_probe_v31 tests.test_radar_jupiter_pacing_v30 tests.test_research_risk_gate_v29 -q && ~/gta6-source/.venv/bin/python -m py_compile research_preentry_evidence_v31.py research_preentry_probe_v31.py
```

## Optional independent, bounded prospective probe

Run against your existing main collector DB/state *read only*, after
the above tests pass, and only if those exact paths exist locally.
It sends a maximum of 2 Jupiter GET quote requests plus one read-only
Solana RPC per selected candidate (subject to retries), with no live
trade side effects:

```bash
~/gta6-source/.venv/bin/python research_preentry_probe_v31.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --state /home/ubuntu/gta6-paper-vps/data/v591/portfolio.json --limit 3
```

If paths differ, do not guess or overwrite anything. Missing inputs
fail closed. No output means no qualifying recent candidate; it does
not indicate safety or absence of meme coins.

## Unresolved before any automated live trading

- Transaction simulation and verifiable end-to-end sell settlement
- Holder distribution, LP control, transferability and program checks
- Prospective sufficient-size token samples, confidence intervals and
  tail-loss limits
- Real exit fees and missing-route stress tests
- Kill switch and cross-service / cross-machine Jupiter quota governance
- Audit of old zombie paper positions and non-executable paper P&L

**Readiness estimates are subjective, not test coverage or probability
of profitability. No claim that the earlier <60% estimates are now >60%.
No production B2/M1 entry rules or active timers changed.**
