
"""GTA6 Collector V6.1 — vroege tokenontdekking en slimme vervolgscans.

Compatibel met bestaande data/learning.db, paper-engine en GitHub Actions.
Bestaande records worden niet verwijderd of opnieuw aangemaakt.
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DB = Path(os.getenv("LEARNING_DB", "data/learning.db"))
LATEST = Path(os.getenv("LATEST_JSON", "data/latest_scan.json"))
API = "https://api.dexscreener.com"

SEARCHES = [
    "GTA6", "GTA VI", "Grand Theft Auto", "Vice City",
    "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn",
]
KEYWORDS = [
    "gta6", "gta 6", "gta vi", "gtavi", "grand theft auto",
    "grandtheftauto", "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn", "raul bautista",
]
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Prioriteit voor tokens met actuele activiteit; de veiligheidsfilters
# van de paper-engine worden hier NIET versoepeld.
ACTIVE_HOURS = 2
ACTIVE_LIMIT = 100
ROTATION_LIMIT = 60
FOLLOW_MIN_LIQ = 5000.0
FOLLOW_MIN_VOL = 50.0

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GTA6-Learning-Research/6.1",
    "Accept": "application/json",
})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value):
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def data_get(path, params=None):
    for attempt in range(3):
        try:
            response = SESSION.get(API + path, params=params, timeout=22)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(DB), timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            address TEXT PRIMARY KEY,
            name TEXT,
            symbol TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            token_address TEXT NOT NULL,
            pair_address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT,
            symbol TEXT,
            price_usd REAL,
            liquidity_usd REAL,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            pair_age_hours REAL,
            average_trade_usd REAL,
            source TEXT,
            PRIMARY KEY (token_address, pair_address, scanned_at)
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS snapshots_lookup
        ON snapshots (token_address, pair_address, scanned_at)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            token_address TEXT NOT NULL,
            pair_address TEXT NOT NULL,
            baseline_at TEXT NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            baseline_price REAL NOT NULL,
            future_price REAL NOT NULL,
            return_pct REAL NOT NULL,
            baseline_liquidity REAL,
            future_liquidity REAL,
            liquidity_change_pct REAL,
            crash_30pct INTEGER NOT NULL,
            liquidity_drop_50pct INTEGER,
            PRIMARY KEY (
                token_address, pair_address, baseline_at, horizon_minutes
            )
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            started_at TEXT PRIMARY KEY,
            finished_at TEXT,
            discovered INTEGER,
            snapshots INTEGER,
            error_count INTEGER,
            errors TEXT
        )
    """)
    connection.commit()
    return connection


def discovery(connection):
    """Nieuwe tokens + actieve follow-up + roterende oudere tokens."""
    addresses = {}
    errors = []

    def add(address, source):
        if isinstance(address, str) and ADDRESS.fullmatch(address):
            addresses.setdefault(address, set()).add(source)

    # Nieuwe vondsten krijgen altijd toegang tot de scan.
    for term in SEARCHES:
        try:
            payload = data_get("/latest/dex/search", {"q": term})
            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                base = pair.get("baseToken") or {}
                title = f"{base.get('name', '')} {base.get('symbol', '')}".lower()
                if any(key in title for key in KEYWORDS):
                    add(base.get("address"), "search")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"search {term}: {str(exc)[:140]}")

    for path in (
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ):
        try:
            payload = data_get(path)
            for item in payload if isinstance(payload, list) else []:
                if item.get("chainId") != "solana":
                    continue
                links = " ".join(
                    str(link.get("url") or "")
                    for link in item.get("links") or []
                    if isinstance(link, dict)
                )
                context = f"{item.get('description') or ''} {links}".lower()
                if any(key in context for key in KEYWORDS):
                    add(item.get("tokenAddress"), "profile")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"profile {path}: {str(exc)[:140]}")

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=ACTIVE_HOURS)
    ).isoformat(timespec="seconds")

    # Laatste beschikbare marktmeting per token. De oudste historische
    # meetpunten worden niet opnieuw als nieuwe activiteit beschouwd.
    active = connection.execute("""
        WITH ranked AS (
            SELECT token_address, scanned_at, liquidity_usd,
                   volume_m5, buys_m5, sells_m5, pair_age_hours,
                   ROW_NUMBER() OVER (
                       PARTITION BY token_address
                       ORDER BY scanned_at DESC, liquidity_usd DESC
                   ) AS position
            FROM snapshots
            WHERE scanned_at >= ?
        )
        SELECT token_address FROM ranked
        WHERE position = 1
          AND COALESCE(volume_m5, 0) >= ?
          AND COALESCE(liquidity_usd, 0) >= ?
          AND COALESCE(buys_m5, 0) > COALESCE(sells_m5, 0)
          AND pair_age_hours BETWEEN 0 AND 24
        ORDER BY
          CASE WHEN volume_m5 >= 250 AND liquidity_usd >= 10000
               THEN 1 ELSE 0 END DESC,
          scanned_at DESC,
          volume_m5 DESC
        LIMIT ?
    """, (cutoff, FOLLOW_MIN_VOL, FOLLOW_MIN_LIQ, ACTIVE_LIMIT)).fetchall()

    for (address,) in active:
        add(address, "active_follow")

    # Houd oude tokens in roulatie; geen records verwijderen.
    rotation = connection.execute("""
        SELECT address FROM tokens
        ORDER BY last_seen ASC
        LIMIT ?
    """, (ROTATION_LIMIT,)).fetchall()
    for (address,) in rotation:
        add(address, "history_rotation")

    print(
        f"V6.1 follow-up: {len(active)} actieve tokens, "
        f"{len(rotation)} rotatietokens; {len(addresses)} totaal ontdekt"
    )
    return addresses, errors


def fetch_pairs(addresses):
    found = {}
    errors = []
    for start in range(0, len(addresses), 30):
        group = addresses[start:start + 30]
        try:
            payload = data_get("/tokens/v1/solana/" + ",".join(group))
            if not isinstance(payload, list):
                raise ValueError("Unexpected response type")
            for pair in payload:
                if pair.get("chainId") != "solana":
                    continue
                base = pair.get("baseToken") or {}
                address = base.get("address")
                pair_address = pair.get("pairAddress")
                if (
                    address not in group
                    or not isinstance(pair_address, str)
                    or not pair_address
                ):
                    continue
                previous = found.get(address)
                liquidity = num((pair.get("liquidity") or {}).get("usd")) or 0
                previous_liquidity = (
                    num((previous.get("liquidity") or {}).get("usd")) or 0
                ) if previous else -1
                if liquidity > previous_liquidity:
                    found[address] = pair
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"batch {start // 30 + 1}: {str(exc)[:140]}")
    return found, errors


def save_snapshots(connection, pairs, sources, stamp):
    recorded = 0
    current_ms = datetime.fromisoformat(stamp).timestamp() * 1000
    for address, pair in pairs.items():
        base = pair.get("baseToken") or {}
        name = str(base.get("name") or "")[:200]
        symbol = str(base.get("symbol") or "")[:60]
        liquidity = num((pair.get("liquidity") or {}).get("usd"))
        volume = num((pair.get("volume") or {}).get("m5"))
        tx = (pair.get("txns") or {}).get("m5") or {}
        buys = int(num(tx.get("buys")) or 0)
        sells = int(num(tx.get("sells")) or 0)
        created = num(pair.get("pairCreatedAt"))
        age = max(0, (current_ms - created) / 3600000) if created else None
        average = (
            volume / (buys + sells)
            if volume is not None and buys + sells > 0 else None
        )
        connection.execute("""
            INSERT INTO tokens(address, name, symbol, first_seen, last_seen)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                name = excluded.name,
                symbol = excluded.symbol,
                last_seen = excluded.last_seen
        """, (address, name, symbol, stamp, stamp))
        connection.execute("""
            INSERT OR IGNORE INTO snapshots(
                token_address, pair_address, scanned_at,
                name, symbol, price_usd, liquidity_usd, volume_m5,
                buys_m5, sells_m5, pair_age_hours, average_trade_usd, source
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            address, pair["pairAddress"], stamp, name, symbol,
            num(pair.get("priceUsd")), liquidity, volume,
            buys, sells, age, average, ",".join(sorted(sources[address])),
        ))
        recorded += 1
    return recorded


def build_outcomes(connection):
    """Behoud bestaande 15-, 30- en 60-minuten leerlabels."""
    inserted = 0
    for horizon in (15, 30, 60):
        baselines = connection.execute("""
            SELECT s.token_address, s.pair_address, s.scanned_at,
                   s.price_usd, s.liquidity_usd
            FROM snapshots s
            WHERE s.price_usd > 0
              AND julianday('now') - julianday(s.scanned_at) >= ? / 1440.0
              AND NOT EXISTS (
                  SELECT 1 FROM outcomes o
                  WHERE o.token_address = s.token_address
                    AND o.pair_address = s.pair_address
                    AND o.baseline_at = s.scanned_at
                    AND o.horizon_minutes = ?
              )
            ORDER BY s.scanned_at DESC
            LIMIT 2000
        """, (horizon, horizon)).fetchall()
        for address, pair, baseline_at, price, liquidity in baselines:
            future = connection.execute("""
                SELECT scanned_at, price_usd, liquidity_usd
                FROM snapshots
                WHERE token_address = ? AND pair_address = ?
                  AND price_usd > 0
                  AND julianday(scanned_at) >=
                      julianday(?) + ? / 1440.0
                  AND julianday(scanned_at) <=
                      julianday(?) + (? + 20) / 1440.0
                ORDER BY scanned_at ASC LIMIT 1
            """, (address, pair, baseline_at, horizon,
                  baseline_at, horizon)).fetchone()
            if future is None:
                continue
            observed_at, future_price, future_liquidity = future
            return_pct = 100 * (future_price / price - 1)
            liquidity_change = (
                100 * (future_liquidity / liquidity - 1)
                if liquidity and future_liquidity is not None else None
            )
            connection.execute("""
                INSERT OR IGNORE INTO outcomes(
                    token_address, pair_address, baseline_at,
                    horizon_minutes, observed_at, baseline_price,
                    future_price, return_pct, baseline_liquidity,
                    future_liquidity, liquidity_change_pct,
                    crash_30pct, liquidity_drop_50pct
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                address, pair, baseline_at, horizon, observed_at,
                price, future_price, return_pct, liquidity,
                future_liquidity, liquidity_change,
                int(return_pct <= -30),
                int(liquidity_change <= -50)
                if liquidity_change is not None else None,
            ))
            inserted += connection.execute("SELECT changes()").fetchone()[0]
    return inserted


def run():
    started = now()
    with connect() as connection:
        addresses, errors = discovery(connection)
        pairs, more_errors = fetch_pairs(list(addresses))
        errors.extend(more_errors)
        recorded = save_snapshots(connection, pairs, addresses, started)
        new_labels = build_outcomes(connection)
        finished = now()
        connection.execute("""
            INSERT OR REPLACE INTO runs
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            started, finished, len(addresses),
            recorded, len(errors), json.dumps(errors),
        ))
        connection.commit()
        total_labels = connection.execute(
            "SELECT COUNT(*) FROM outcomes"
        ).fetchone()[0]
        total_snapshots = connection.execute(
            "SELECT COUNT(*) FROM snapshots"
        ).fetchone()[0]

    LATEST.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_at_utc": started,
        "finished_at_utc": finished,
        "discovered": len(addresses),
        "recorded": recorded,
        "new_labels": new_labels,
        "total_labels": total_labels,
        "total_snapshots": total_snapshots,
        "errors": errors[:25],
        "note": (
            "Research only. Missing observations do not prove "
            "safety or price outcome."
        ),
    }
    LATEST.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    if recorded == 0 and errors:
        raise RuntimeError("No snapshots recorded; API errors detected")


if __name__ == "__main__":
    run()
