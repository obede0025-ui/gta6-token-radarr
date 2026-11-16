# GTA6 unified analysis — one command instead of separate report paste

This **offline, read-only** report combines:
1. Last 3,000 minute-market observation statuses and missing liquidity rate.
2. Discovery V1 baseline (30) vs expanded (<=100), latest and cumulative.
3. Discovery V2 independent feed latest/recent quality, failed base gates,
   spaced-volume-momentum reasons, and unique shadow-signal counts.
4. Phase3 B3→M1 pilot run count and internal handoff latency.
5. One concrete research priority.

Reasons are **not mutually exclusive**. Missing liquidity is not zero;
any unknown data fails closed. Pair age measures pool age, not token mint age.
A momentum pass is a shadow proxy only; not an approved trade.
Output is offline (0 HTTP), no timers/trades/DB or frozen cohort changes.

## Single VPS command

After running any V2 research cycles you want to include:

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_unified_analysis -q && ~/gta6-source/.venv/bin/python research_unified_analysis.py
```

Paste the **single JSON report** here. No need to run V1, V2, B3 and
phase3 report commands separately. All source files remain unchanged.
If you need a persistent report file for upload, use
`python research_unified_analysis.py > unified_report.json`
(the output file is new; source DBs are not modified).

An empty source section is reported as MISSING/EMPTY, not invented data.

## Caution

This report does not launch a scan: it reads whatever observations already
exist. Discovery V1/V2 manual-only shadow pipelines need to have been
previously run for their prospective momentum comparisons. A single manual
run cannot prove momentum, and 2 cycles are insufficient to justify replacing
the original selection. New candidate sources must be tested independently
before any automation or B3/M1 production integration.
