# Discovery V2.5 — bounded 60-second prospective trial

Manual experiment, not a daemon. Default 10 attempts at no less than
60 seconds start-to-start. 2–30 attempts permitted; 60–300 second
interval only. Stop after 3 consecutive failed API/DB attempts.
Slow cycles never trigger a catch-up API burst.

**Uses a separate SQLite** `discovery_v25_minute_shadow.sqlite`.
This is intentionally NOT `discovery_quality_v22.sqlite`; V2.2
research, production `learning.db`, B3/M1, service units, timers and
frozen cohorts remain intact. Uses V2.2's exact same 60-token feed
universe, two 20-slot FEED_ORDER vs ACTIVITY_FIRST cohorts,
market quality gates and same-pair/cohort spaced rolling m5 momentum
proxy. Maximum expected network calls per attempt: 2 latest feed calls
plus up to 2 batches of tokens. No orders or wallet API.

**One VPS command** (foreground trial; terminal needs to remain open):

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_minute_v25 -q && ~/gta6-source/.venv/bin/python research_discovery_minute_v25.py --cycles 10 --interval 60 --slots 20
```

The final JSON compares cohort counts **for this run only**, observed
interval gaps, error count, and an offline V2.4 early-activity audit.
It runs in the foreground. Ctrl-C stops manual trial; no timer installed.

Important limits: token selection can change between cycles; recorded
cycle spacing is not proof that each exact mint/pair/cohort was
successfully sampled at that cadence. Five-minute rolling volume and
buys/sells counts overlap across adjacent one-minute snapshots. Any
early-watch or V2 momentum proxy is research-only and not a trade
approval, profit forecast or validated B3 result.
