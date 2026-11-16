# Discovery V2.7 — first-signal route quote paper trial

Uses Jupiter's current `GET https://api.jup.ag/swap/v2/order` with
**no taker, no wallet, and no API key required** at the documented
keyless 0.5 RPS limit. Only GET requests; no transaction is created,
signed or sent. The quote provider can be unavailable or unable to route
some Solana memecoins. The quote API can route through *different
pools* from the original discovery pair.

**Do not confuse quote availability with trade execution.** The
hypothetical buy amount is **10 USDC**, encoded as 10,000,000 raw USDC
units. The first quote yields hypothetical raw token units, and a
separate immediate reversal quote tests route availability. At 1, 5 and
15 minutes following that first signal, new Jupiter token-to-USDC
quote-only requests evaluate selling those same hypothetical token units.
Theoretical return is `(sellQuoteUSDC / 10 USDC - 1) * 100`;
this includes the quoted route output but not gas, failure/slippage
between quote and inclusion, token taxes, or any actual executed fill.

V2.7 freezes **only the first fresh signal per exact token mint and
discovery pair, regardless of cohort**. Both V2.2 cohorts may appear in
the single signal's metadata. This prevents repeated signal timing
for the same token/pool from falsely inflating outcomes. Coin identity
across multiple pools is not consolidated.

A new standalone 25-cycle experiment first runs V2.2 discovery for 10
cycles and continues quote follow-ups until the remaining 15-minute
windows close. Writes exclusively to:
- `discovery_v27_source.sqlite`
- `discovery_v27_quotes.sqlite`

No changes to V2.6 files, B3/M1, frozen cohorts, existing services or
timers. No keys need to be pasted into chat.

## Single VPS command

```bash
cd ~/gta6-v32-gate-pilot && git pull --ff-only origin feature/v32-preentry-gate-pilot && ~/gta6-source/.venv/bin/python -m unittest tests.test_research_discovery_quote_v27 -q && ~/gta6-source/.venv/bin/python research_discovery_quote_v27.py
```

Runs in the foreground with 25 scheduled cycles at ~60 seconds,
possibly longer if quote queries take time. Terminal must remain open.
The script stops after three consecutive errors; printout remains one
report at the end. The test has not been run on your VPS until you run
the command.

To read the previously created V2.7 report without more API calls:
```bash
~/gta6-source/.venv/bin/python research_discovery_quote_v27.py --report-only
```

An existing V2.7 experiment refuses overwriting files. Use new
`--source` and `--outcomes` names for any separate experiment
rather than deleting older cohorts.

Jupiter API reference:
https://developers.jup.ag/docs/swap/index
