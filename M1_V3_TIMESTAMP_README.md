# M1 V3.3 — precieze timestamp-instrumentatie

Vanaf deze commit registreren we nieuwe verwerkingstijdstempels, **zonder bestaande staten te verwijderen**:

- B3: `recorded_utc` bij evaluatie van de rij, niet op moment van oorspronkelijke snapshot.
- M1 V3: `registered_utc` en doorgifte `b3_recorded_utc`.
- Jupiter M1 V3.2: `first_seen_utc`, plus doorgifte M1/B3-tijdstempels.
- Latencyrapport: B3 → M1-registratie en M1 → V3.2-ontvangst, mits nieuwe timestamps bestaan.

Oudere signalen hebben geen `b3_recorded_utc`; rapportage toont daarvoor `null` in plaats van vertraging te verzinnen. De historische B3-snapshotdatum blijft behouden. `recorded_utc` is het moment van evaluatie in B3, niet noodzakelijk de persistente schrijf-/publicatietijd; precieze levering is dus nog steeds benaderend.

Deze wijziging introduceert **geen extra netwerkverzoeken of timers**. De bestaande V3/V3.2 volgen de nieuwe velden zodra code op de VPS is gepulld.

```bash
cd ~/gta6-v63-test
~/gta6-source/.venv/bin/python -m unittest discover -s tests -p 'test_m1_v3_timestamp_pipeline.py' -v
~/gta6-source/.venv/bin/python m1_v3_latency_audit.py \
  --b3 b3_state.json --m1 m1_v3_state.json --v32 m1_v3_2_state.json \
  --output m1_v3_latency_report.json
```
