# Experimental B3 candidate audit

Standalone **read-only** candidate screening with stricter liquidity ($20,000), 5m volume ($250), buy/sell imbalance and B momentum check. Examines recent snapshots only (last 150 seconds), without adding papertrades, changing A/B/C/B2, or placing orders.

Historical comparisons are NOT evidence of forward performance. Even passing B3 prefilters does not mean a mint is safe or tradable; Jupiter quote tests, token-contract security and fill simulation remain necessary.

```bash
python3 -m unittest discover -s tests -p 'test_*v63.py' -v
python3 -m unittest discover -s tests -p 'test_b3_candidate_audit.py' -v
python3 b3_candidate_audit.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --output b3_candidate_report.json
```

Do not schedule until prospective monitoring, risk limits and tests are complete.
