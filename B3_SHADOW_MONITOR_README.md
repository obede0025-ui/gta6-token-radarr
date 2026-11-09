# B3 prospective shadow recorder (experimental)

- Explicit bootstrap on first run: cursor starts at the latest existing market snapshot and **never backfills historical candidate results**.
- On later runs: records new database snapshots and reasons for rejection into its **own** `b3_state.json` state, capped at 10,000 retained events, plus cumulative observation count.
- Snapshots older than 15 minutes at processing time are skipped; their cursors are advanced to avoid retrospective results.
- The SQLite market database is opened **read-only**; A/B/C/B2 portfolios and services are unchanged.
- No wallet, real orders or virtual trade execution. Future return tracking, token security gates, Jupiter quotes and execution costs are **not yet implemented**.
- This script is not safe to run concurrently against the same state file; use `flock` if scheduling it.

## Tests
```bash
python3 -m unittest discover -s tests -p 'test_b3_shadow_monitor.py' -v
```

## Manual smoke tests on isolated VPS clone (not production)
```bash
python3 b3_shadow_monitor.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --state /home/ubuntu/gta6-v63-test/b3_state.json
```
First run should say `bootstrapped: true`, then later runs should record only new observations. **Do not schedule** until validated with VPS state isolation, service locking, and monitoring.
