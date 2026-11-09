# M1 V3.1 — handmatige Jupiter-quote-audit (alleen lezen)

Leest de reeds bestaande M1 V3-status; verandert geen bestaande bot of timer.

- Inspecteert maximaal twee M1-tokens met status `WATCHING` of `OBSERVED_PRICE_TRIGGER`.
- Vraagt per token maximaal twee quote-only Jupiter Swap V2-aanvragen op: hypothetische aankoop voor 25 USDC en onmiddellijke verkoop van die **zojuist gequote hoeveelheid**. **Geen** wallet, `taker`, ondertekening of verzending.
- Berekent onmiddellijke indicatieve roundtrip-verandering. Dit is **geen opbrengst van instap bij B3-signaal** en **geen gerealiseerde PnL**.
- Quote-gegevens missen uitvoeringszekerheid, netwerk-/priority fees, eventuele accountkosten en prijsbeweging tot executie.
- Maximaal twee tokens per handmatige uitvoering; cache van 15 minuten voor tokens in het vorige rapport (geen gedeelde quota over processen).
- Bewaart **nooit** API-sleutel of ruwe Jupiter-antwoorden.
- Deze module gebruikt de bestaande `radar_quote_monitor.quote()` en vereist de variabele `JUPITER_API_KEY` in de shellomgeving.

```bash
python3 -m unittest discover -s tests -p 'test_m1_v3_1_quote_audit.py' -v
# Sleutel via bestaande private env-file, maar nooit de inhoud printen:
set -a
source /home/ubuntu/.config/gta6-radar/jupiter.env
set +a
python3 m1_v3_1_quote_audit.py \
  --state /home/ubuntu/gta6-v63-test/m1_v3_state.json \
  --output /home/ubuntu/gta6-v63-test/m1_v3_1_quote_report.json
unset JUPITER_API_KEY
```

**Deze versie draait niet automatisch.** Eerst tests en handmatig API-gedrag verifiëren. Bij 0 actieve kandidaten doet het programma geen verzoeken.
