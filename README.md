# gta6-token-radarr
GTA 6 Solana Memecoin Early Discovery Scanner

## Snelle marktmonitor (V6.22) — 24/7-serverklaar, nog niet geactiveerd

De bestaande GitHub Actions collector blijft afzonderlijk iedere ~5 minuten draaien.
De nieuwe **alleen-lezen** servermonitor volgt open paperposities (A/B/C/B2)
en ontdekte GTA6-gerelateerde Solana-tokens iedere 60 seconden.
De lijst met open paperposities wordt om de 5 minuten opnieuw uit de **publieke**
GitHub-repository gelezen, en nieuwe tokens worden om de 2 minuten gezocht.
De monitor gebruikt geen lokale SQLite-database en schrijft **niets** naar
`data/`, portfolio's, GitHub of een wallet. Er worden geen orders geplaatst.
Een frequenter marktsignaal garandeert geen betere uitvoering of winst.

### Eerst één cyclus testen

Op een Ubuntu VPS met Python 3.11+ en Git geïnstalleerd:

```bash
git clone https://github.com/obede0025-ui/gta6-token-radarr.git
cd gta6-token-radarr
python3 -m venv .venv
.venv/bin/pip install -r requirements-collector.txt
.venv/bin/python collector.py --fast-monitor --server --once
```

Verwachte JSON-uitvoer: `mode: FAST_MONITOR_SERVER_READ_ONLY`, aantallen
`tracked`, `observed`, `open_positions`, plus `alerts` en `errors`.
Bij API-storingen kunnen `observed` en de alerts onvolledig zijn.
De test bewijst geen ononderbroken 24/7-werking.

### Continu starten op een VPS

```bash
cd ~/gta6-token-radarr
.venv/bin/python collector.py --fast-monitor --server --interval 60
```

Om dit ook na het sluiten van SSH en na een herstart te blijven draaien,
gebruik je een process manager, bijvoorbeeld systemd. Maak **op de server**
een systemd-unit aan (geen extra repo-bestand nodig), met het correcte
Linux-gebruikersaccount en absolute projectpaden:

```ini
[Unit]
Description=GTA6 read-only fast market monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/gta6-token-radarr
ExecStart=/home/ubuntu/gta6-token-radarr/.venv/bin/python -u collector.py --fast-monitor --server --interval 60
Restart=always
RestartSec=15
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Sla dit op als `/etc/systemd/system/gta6-fast-monitor.service` en voer uit:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gta6-fast-monitor
sudo systemctl status gta6-fast-monitor
journalctl -u gta6-fast-monitor -f
```

Vervang `ubuntu` en `/home/ubuntu` door de echte VPS-gebruiker en
het projectpad. Voer de monitor niet tweemaal tegelijk uit. Elke
GitHub-codewijziging vereist op de VPS een veilige update en een
serviceherstart. Geef de monitor **geen** GitHub-schrijftokens,
Solana-walletsleutels of Jupiter-geheimen.

### Grenzwaarden en aandachtspunten

- Frequentie: `--interval 30` t/m `--interval 300` seconden;
  60 seconden is de aanbevolen startinstelling.
- Maximaal 30 tokens per cyclus, met open posities eerst.
- Waarschuwingen bij een prijsdaling van 20%, een
  liquiditeitsdaling van 40%, wisseling van pair of lage liquiditeit.
- Portfolioherlaadinterval 300 seconden; discovery 120 seconden.
- Een JSON-regel per cyclus wordt op stdout gelogd; er is **geen**
  ingebouwde pushmelding, automatische exit of opslag in GitHub.
- Bij herstart verdwijnen de prijsbaselines uit het werkgeheugen;
  de eerste cyclus heeft daarom nog geen vergelijkingswaarschuwingen.
- De monitor leest publieke `raw.githubusercontent.com`-data.
  Een private repository of onbereikbare GitHub/Dexscreener API
  vraagt een andere, veilige gegevensbron.
- Bestaande A/B/C/B2-paperresultaten en `learning.db` blijven ongewijzigd.
- De server is **nog niet aangemaakt of ingezet**; deze instructies
  en de code maken een latere 24/7-installatie mogelijk.
