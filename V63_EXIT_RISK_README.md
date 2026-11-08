# V6.3 – Exit Risk Audit (experimental)

This branch is isolated from production. Do **not** merge or deploy automatically.

## Scope
- Quotes are *indicative*, not filled trades or guaranteed sellability.
- Existing collector, paper engines, systemd services and portfolios are unchanged.
- `exit_risk_v63.py` classifies an existing Jupiter quote JSON locally, without API calls.
- `quote_refresh_v63.py` refreshes quotes only if a prior successful report is older than 15 minutes; it requires `JUPITER_API_KEY` to be set in the process environment.
- `HTTP_429`, invalid, missing and stale quotes are **unknown**, never zero value.
- Do not add the API key to Git or logs.

## Offline test
```bash
python3 -m unittest discover -s tests -p test_exit_risk_v63.py -v
python3 exit_risk_v63.py --input data/jupiter_quote_test.json --output data/v63_exit_audit.json
```
The sample `data/jupiter_quote_test.json` is on the VPS, not in Git.

## Controlled quote refresh (manual, separate from working services)
```bash
python3 quote_refresh_v63.py --state data/v591/portfolio.json --output data/v63_quotes.json --min-seconds 900
python3 exit_risk_v63.py --input data/v63_quotes.json --output data/v63_exit_audit.json
```
Run only in a private, dedicated test directory with your key already supplied via a protected environment file. This version does not yet implement per-call pacing inside the existing quote monitor; `429` responses may still occur during a refresh. The wrapper does **not** retry failed/partial requests automatically. Do not schedule it until API pacing, backoff, and failure handling are validated.

Do not feed these classifications into live trading or into the existing A/B/C/B2 paper strategies. Backtesting, quote sizing, freshness, execution costs and wallet safety require further validation.
