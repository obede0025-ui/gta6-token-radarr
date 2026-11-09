# M1 V3.3 — Latency Audit (geen services wijzigen)

Read-only lokaal overzicht van de signaalketen B3 → M1 V3 → Jupiter V3.2. **Geen Jupiter-aanvragen** en geen automatische orders.

```bash
cd ~/gta6-v63-test
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_v3_latency_audit.py' -v
~/gta6-source/.venv/bin/python m1_v3_latency_audit.py \
  --b3 /home/ubuntu/gta6-v63-test/b3_state.json \
  --m1 /home/ubuntu/gta6-v63-test/m1_v3_state.json \
  --v32 /home/ubuntu/gta6-v63-test/m1_v3_2_state.json \
  --output /home/ubuntu/gta6-v63-test/m1_v3_latency_report.json
```

Let op: het B3-signaal is de tijd van **goedkeuring**, niet de lanceringstijd. De eerste M1-marktmeting is slechts een **benadering** van registratie; exacte ontvangsttijd wordt niet gelogd en oude samples kunnen zijn begrensd. Zowel M1 V3 als V3.2 starten forward-only. Ook oude signalen mogen niet opnieuw als quotes op het oorspronkelijke tijdstip worden beschouwd.

Deze module maakt onderscheid tussen `SKIPPED_STALE`, `NOT_RECORDED` en `ACTIVE`; bouw pas op basis van deze resultaten aan de echte bottleneck.
