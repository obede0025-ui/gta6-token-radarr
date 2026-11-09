# M1 — Micro-activiteit en venster van 15 minuten (onderzoeksprototype)

Vergelijkt eerste B3-kwalificatie per unieke token. MICRO: >1000 transacties/5min, gemiddelde trade <$2, koop/verkoopverhouding 0,95–1,15. OTHER = overige B3-kandidaten.

Vaste **onderzoeksparameters**: 15% take-profit, -10% stop-loss, uiterlijk 15min time-exit, maximaal 3min tijdspeling om een werkelijke vervolgmeting te vinden. Deze waarden zijn **niet geoptimaliseerd**, maar demonstreren kort-window monitoring. Omdat snapshots niet continu zijn, detecteert dit alleen waargenomen TP/SL-momenten en kan een echte exit voor of na de grens totaal anders uitpakken.

Dit is een **historische pilot**, geen handelsbot en geen garanties. Geen Jupiter-quote, buy/sell fees, gas, slip, wallet, orderuitvoering of een gesimuleerde orderboekfill. Een ontbrekende quote wordt nooit als nul- of succesvolle exit behandeld. Data is alleen-lezen. B3 blijft onveranderd.

```bash
python3 -m unittest discover -s tests -p 'test_m1_micro_15m_research.py' -v
python3 m1_micro_15m_research.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --shadow /home/ubuntu/gta6-v63-test/b3_state.json --output /home/ubuntu/gta6-v63-test/m1_micro_15m_report.json
```
