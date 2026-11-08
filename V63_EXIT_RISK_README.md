# V6.3 Experimental exit-risk monitoring

This is an isolated **manual test branch**. Do not merge or deploy as a production timer yet.

- `exit_risk_v63.py` evaluates locally saved quotes without network access.
- `v63_quote_monitor.py` wraps the existing quote-only Jupiter module and spaces Jupiter quote calls by at least 1.5 seconds, retrying HTTP 429 at most twice with bounded backoff.
- `quote_refresh_v63.py` only writes complete `OK` reports, caching successful output for at least 15 minutes.
- API limits are shared across clients; pacing in this **one process** cannot prevent rate limits caused by other callers. A PARTIAL report is not cached.
- Never use stale, unknown, partial, or 429 quotes as a price of zero. Exit quotes are indicative and exclude execution/network cost.
- No wallet, signing, or real orders. API key belongs outside Git. Neither existing systemd service is modified.

## Offline tests (no Jupiter API calls)
```bash
python3 -m unittest discover -s tests -p 'test_*_v63.py' -v
```

## Optional manual quote run in private test clone
```bash
python3 -m pip install -r requirements-collector.txt
# Supply JUPITER_API_KEY privately; do not paste it in chat or commit it.
python3 quote_refresh_v63.py --state data/v591/portfolio.json --output v63_quotes.json --min-seconds 900
python3 exit_risk_v63.py --input v63_quotes.json --output v63_audit.json
```
Only run the audit if a complete `v63_quotes.json` exists and is recent. Do not schedule refresh until error handling and shared quotas are validated. 
