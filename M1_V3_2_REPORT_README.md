# M1 V3.2 — geïntegreerd quote-prestatierapport

Het rapport leest de bestaande `m1_v3_2_state.json` en doet **geen API-aanvragen**. Geen wijzigingen in timers of paperportefeuilles.

Per token: status, instapvertraging, aantal beschikbare en ontbrekende verkoopquotes, slechtste/beste quote, eerste waargenomen break-evenquote en laatst geobserveerde quote binnen 15 minuten na B3-signaal.

Break-even betekent uitsluitend een **indicatieve quote vanaf 25 USDC**, zonder netwerk-/priority fees, latere slippage of werkelijke uitvoering. De beste waargenomen quote is niet noodzakelijk realiseerbaar. De aanwezigheid van een quote buiten het oorspronkelijke venster telt niet mee.

```bash
cd ~/gta6-v63-test
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_v3_2_quote_report.py' -v
~/gta6-source/.venv/bin/python m1_v3_2_quote_report.py \
  --state m1_v3_2_state.json \
  --output m1_v3_2_performance_report.json
```
