# Discovery Coverage / Missed Opportunities — read-only

De hoeveelheid **unieke tokens** is belangrijker dan het aantal herhaalde scans. Dit rapport toont de funnel: cumulatieve database-ontdekking, B3-scans, unieke B3-goedgekeurde kandidaten, M1-opvolging, M1-capaciteitsverliezen en Jupiter V3.2-instap-/verkoopquotes.

De exacte cohortanalyse gebruikt uitsluitend B3-rijen met `recorded_utc` na de initialisatie van M1. Oude B3-signalen worden niet ten onrechte als gemiste M1-kansen meegeteld.

```bash
cd ~/gta6-v63-test
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_discovery_funnel.py' -v
~/gta6-source/.venv/bin/python m1_discovery_funnel.py \
  --b3 b3_state.json \
  --m1 m1_v3_state.json \
  --v32 m1_v3_2_state.json \
  --db /home/ubuntu/gta6-paper-vps/data/learning.db \
  --output m1_discovery_funnel_report.json
```

Geen nieuwe netwerkverzoeken, geen API-sleutels, geen wijzigingen in productie of timers. Scanfrequentie of filters niet verhogen voordat duidelijk is waar unieke kandidaten verloren gaan. Meer tokens is niet noodzakelijk meer rendement; let op selectie, verkoopbaarheid en kosten.
