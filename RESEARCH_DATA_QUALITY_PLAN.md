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
