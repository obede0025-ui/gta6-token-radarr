# Phase 3 isolated event-driven handoff pilot

The pilot sequentially runs the existing minute B3 and M1 **research-only** Python
functions with unique state files in `handoff_phase3_pilot/`. It uses the
existing minute-market SQLite database **read-only**, and calls **no Jupiter API**.
The original B3, M1, quote services and timers stay unchanged.

## 1. Pull and test (nothing activated)

```bash
cd ~/gta6-v32-gate-pilot
git pull --ff-only origin feature/v32-preentry-gate-pilot
~/gta6-source/.venv/bin/python -m unittest tests.test_research_minute_handoff_phase3 tests.test_research_minute_handoff_phase2 tests.test_research_minute_handoff_timing -v
```

## 2. First isolated manual cycle

```bash
mkdir -p ~/gta6-v32-gate-pilot/handoff_phase3_pilot
~/gta6-source/.venv/bin/python research_minute_handoff_phase3.py
~/gta6-source/.venv/bin/python research_minute_handoff_phase3.py --report-only
```

The first run only bootstraps independent states, deliberately excluding all prior
market observations and old research signals. Subsequent runs consume new DB samples.
No existing state files should be deleted/reset.

## 3. Optional automatic pilot (explicit separate operator action)

Only after successful tests/manual runs:

```bash
sudo install -m 0644 systemd/gta6-handoff-phase3-pilot.service /etc/systemd/system/
sudo install -m 0644 systemd/gta6-handoff-phase3-pilot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gta6-handoff-phase3-pilot.timer
systemctl status gta6-handoff-phase3-pilot.timer --no-pager
```

Original timers remain active; pilot writes only to its own files.

**Stop / rollback:** `sudo systemctl disable --now gta6-handoff-phase3-pilot.timer`
and optionally `sudo systemctl stop gta6-handoff-phase3-pilot.service`.
Do not delete any state. This only stops the isolated pilot.

## 4. Comparison

```bash
~/gta6-source/.venv/bin/python research_minute_handoff_phase3.py --report-only
sudo journalctl -u gta6-handoff-phase3-pilot.service --since "1 hour ago" --no-pager | tail -40
```

Observed legacy B3-to-M1 median service scheduling delay was 31 seconds over 116
paired cycles. The pilot records **B3 function return to M1 function start** in
milliseconds; these are NOT identical start/end-to-start/end clock measures.
With fresh prospective candidates, check M1 admission and coverage separately.
No profit, actual fill, new token discovery latency, or execution advantage is
inferred from an improved handoff measure.

## Constraints

- Do not point pilot JSON paths at original production states.
- Do not activate simultaneous pilot instances; the nonblocking flock skips them.
- Do not disable or modify original timers.
- The independent B3 pilot still applies the current read-only B3 research rules.
- The pilot is explicitly NOT authorized for real trades.
