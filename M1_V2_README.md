# M1 V2 geïntegreerd onderzoeksrapport

Eén offline rapport bundelt de **eerste B3-kwalificatie per unieke token**, pre-signaal volume, koopoverschot en liquiditeit, plus de bestaande M1-hypothetische 15-minuten-koersuitkomst.

De uitputtingswaarschuwing is uitsluitend **hypothese**: >60% volumedaling en >50% afname in koopoverschot gedurende het beschikbare venster van maximaal 15 minuten. Omdat deze grenswaarden na historische voorbeelden zijn gekozen, mag geen voorspellende waarde worden geclaimd. Geen tokenblokkering, geen echte of virtuele trades.

Let op: 15 minuten is het onderzoeksvenster, maar de bestaande M1-snapshotanalyse accepteert een observatie tot 3 minuten erna. Een resultaat is dus niet noodzakelijk een uitvoerbare uitstap binnen 15 minuten.

Tests en rapport:
```bash
python3 -m unittest discover -s tests -p 'test_m1_v2_diagnostic.py' -v
python3 m1_v2_diagnostic.py --db /home/ubuntu/gta6-paper-vps/data/learning.db --shadow /home/ubuntu/gta6-v63-test/b3_state.json --output /home/ubuntu/gta6-v63-test/m1_v2_report.json
```

Geen service-installatie nodig; production blijft ongewijzigd. Volgende fase is uitsluitend vooruitkijkende waarneming met kosten/quote-evaluatie.
