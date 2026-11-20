# GTA6 Research: tekortkomingen en acceptatiecriteria

Status: onderzoeksfase; geen live trading. Deze branch voegt alleen een read-only audit toe.

| Prioriteit | Tekort | Vastgesteld | Verificatie voordat we het opgelost noemen |
|---|---|---|---|
| P0 | Datakwaliteit eerste snapshot | 3.519 / 4.208 tokens hadden onbruikbare startprijs of -liquiditeit in een historische test | Oorzaak per categorie identificeren; latere correcte data nooit stilzwijgend als initiële data gebruiken |
| P0 | Datasetverschillen | 8 / 41 historische kandidaten met bruikbare 60–90s meting in minuut-DB | Cohorten en tijdsvensters consistent vergelijken; overlappende mints/pairs meten |
| P0 | Executie en netto P/L | DexScreener-prijzen zijn geen fills | Prospectieve buy/sell quotes en kosten, zonder orders, voor interpretatie van theoretische rendementen |
| P0 | Selectiebias en overfitting | Patronen retrospectief gekozen; eerder geen gevalideerde strategie | Bevroren regels op werkelijk nieuw, nog ongezien tijdsblok testen |
| P1 | Late ontdekking | Mediane geschatte paarleeftijd bij ontdekking 5,22 min in onderzochte groep | Blockchain-tijdstip, externe beschikbaarheid en eerste lokale registratie onderscheiden |
| P1 | Trage historische sampling | Ca. 307 sec tot tweede snapshot bij 41 kandidaten | Historische marktinterval meten en daadwerkelijke hiaten per token rapporteren |
| P1 | Crashdetectie | Gecombineerde waarschuwing zag 9 / 97 liquiditeitsproblemen na 15 min | Echte LP-daling >=80% los van LP < $20k rapporteren; precision/recall én gemiste winnaars |
| P2 | Wallethistorie | Geen aantoonbaar bewaarde wallet-/transactiehistorie in onderzochte DB's | Alleen indien opnieuw beschikbaar: bewijslast van transacties, gerealiseerd en ongerealiseerd resultaat verifiëren |

## Eerste verbetering op deze branch

`research_data_quality_audit.py` leest de twee bestaande databases **alleen-lezen**, maakt geen nieuwe databases aan en rapporteert:

- oorzaak van ongeldige eerste snapshots (prijs en liquiditeit apart, plus gezamenlijk);
- ontdekkingsbron en gerapporteerde paarleeftijd per bron;
- mediane intervallen van opeenvolgende snapshots per paar;
- aantal tokens dat in beide databases aanwezig is;
- ontbrekende prijzen, liquiditeit en tijdstempels in minuutdata.

Uitvoeren na review (geen services stoppen of aanpassen):

```bash
cd ~/gta6-v32-gate-pilot
~/gta6-source/.venv/bin/python research_data_quality_audit.py
```

**Beperkingen:** all-time gegevens zijn niet hetzelfde cohort als een 7-daagse studie; een eerste snapshot is geen tokenlancering; een overlap in mints alleen bewijst niet dat dezelfde vroege observaties bestaan. Deze audit bewijst geen handelsvoordeel en wijzigt geen filters.

## Update: liquiditeit per marktstatus (20 november 2026)

Live read-only telling uit `minute_market_pilot.sqlite` (alle statussen):

| Status | Observaties | LP NULL | LP 0 | LP > 0 | Prijs NULL |
|---|---:|---:|---:|---:|---:|
| QUOTE_MARKET_OBSERVED | 204.185 | 0 | 57.283 | 146.902 | 0 |
| LIQUIDITY_UNKNOWN | 136.023 | 136.023 | 0 | 0 | 0 |
| MARKET_DATA_UNAVAILABLE | 7.552 | 7.552 | 0 | 0 | 7.175 |

**Belangrijk:** deze all-status telling is niet gelijk aan de bestaande auditcohort voor `QUOTE_MARKET_OBSERVED`. Een LP-waarde van 0 bij die status is nog geen bewijs van rug pull of echte poolleegloop. Geen historische koersuitkomst of crashlabel mag uit NULL worden afgeleid. De audit rapporteert voortaan de splitsing per status; handels- en crashfilters blijven ongewijzigd tot de producer van nulwaarden is geverifieerd.

## Codeonderzoek producer (20 november 2026)

`research_minute_market_pilot.py` `extract()` neemt `liquidity.usd` uit de DexScreener-payload via `finite()` over, en `record()` markeert een waarde `0` als `QUOTE_MARKET_OBSERVED` wanneer de prijs positief is. `None` wordt `LIQUIDITY_UNKNOWN`. Dit verklaart classificatie in de database maar **bewijst geen werkelijke lege pool**.

Toegevoegd: `research_zero_liquidity_persistence.py` + tests voor observatie van nul na 5 minuten en m5-volume/transacties in een later venster. Het script mag niets schrijven en wijzigt geen tradinglabels, orders of services. Een nulstatus blijft **onbevestigd** totdat een onafhankelijke marktbewijsbron is gecheckt. Bij hypothetische nieuwe instappen moet ontbrekende of nul-liquidity fail-closed blijven; deze PR implementeert geen actieve instapcontrole.

## Beschrijvende liquiditeitsstatus (research-only)

`research_liquidity_states.py` classificeert een positieve quote-liquiditeit als `LIQUIDITY_POSITIVE_OBSERVED`, API-nul als `LIQUIDITY_ZERO_UNVERIFIED` en ontbrekende bronvelden als `LIQUIDITY_UNKNOWN`. Ongeldige (negatieve/niet-eindige) getallen krijgen `LIQUIDITY_INVALID`. Positieve LP is geen onafhankelijke on-chain bevestiging en geen handelsgoedkeuring. De classifier is bewust niet aan de actieve collector, entry filter of paper/live orders gekoppeld. Nieuwe tests: `tests.test_research_liquidity_states`.

## Drie concrete cases: read-only publieke RPC probe

`research_public_solana_pool_probe.py` gebruikt uitsluitend publieke `getMultipleAccounts` en `getSignaturesForAddress` RPC-read calls voor drie vooraf geselecteerde historische paren (aanhoudend nul, beperkt herstel, vervolgmeting ontbreekt). Het script onthult het **huidige** owner-programma van het account en of recente transactiegeschiedenis in een beperkte steekproef het historische moment raakt. Het registreert geen historische reserves en houdt de classificatie op `UNVERIFIED`. De publieke RPC kan worden beperkt of historiek missen; gebruik geen betaald RPC-adres in gedeelde logs.

Op de aparte VPS-testkopie: `python -m unittest tests.test_research_public_solana_pool_probe -v` en `python research_public_solana_pool_probe.py`. Echte verificatie vereist daarna programma-afhankelijke reserveaccount-identificatie en onafhankelijke slot-historische tokenbalansen.

## Historische PumpSwap-transactiemetadata — onderzoeksfase

`research_pumpswap_transaction_meta.py` zoekt via publieke `getSignaturesForAddress` twee transacties voor en twee na de nulmeting voor ieder van drie paren, en leest `getTransaction` metadata (`preTokenBalances`, `postTokenBalances`). Verschillen worden als exacte ruwe tokenbedragen vastgelegd. Deze accountdeltas zijn **geen bevestigde PumpSwap-reserves** totdat de base/quote-vaultaccounts onafhankelijk zijn geïdentificeerd. Ontbrekende tokenmetadata wordt niet als nul geïnterpreteerd. Alle labels blijven `UNVERIFIED`; geen services of strategieën worden aangepast. Tests: `tests.test_research_pumpswap_transaction_meta`.
