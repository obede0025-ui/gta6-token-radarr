# M1 V3 — eerste vooruitkijkende onderzoeksmonitor

Beperkte handmatige pilot. Volgt uitsluitend **nieuwe B3-goedgekeurde kandidaten**; geen historische replay. Meet indicatieve marktprijzen via Dexscreener voor de exacte Solana-mint én pair. Maximaal drie gelijktijdige kandidaten, max. drie aanvragen per run en minstens 60 seconden pauze per token. Venster van 15 minuten start bij B3-signaal, ook bij vertraagde detectie.

+15% en −10% zijn alleen **waargenomen koers-triggers**, nooit uitvoerbare TP/SL of winstgaranties. Geen wallet, orders, werkelijke paperfills, Jupiter-verkoopquotes, fees of slippage. Deze versie volgt nog geen aparte waarschuwing voor uitputtend momentum. Niet automatisch plannen voordat de beperkingen en het falen van queries zijn onderzocht.

```bash
python3 -m unittest discover -s tests -p 'test_m1_v3_forward_monitor.py' -v
python3 m1_v3_forward_monitor.py --shadow /home/ubuntu/gta6-v63-test/b3_state.json --state /home/ubuntu/gta6-v63-test/m1_v3_state.json
```
