import os
import re
import json
import time
import uuid
import sqlite3
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


# ============================================================
# GTA 6 TOKEN RADAR V5.7.1
# Scanner + Strategie A/B + Papertrades + Supabase Backup
# ============================================================

st.set_page_config(
    page_title="GTA 6 Radar V5.7.1",
    page_icon="🎮",
    layout="wide"
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
API = "https://api.dexscreener.com"
LOCAL = ZoneInfo("Europe/Brussels")

SEARCHES = [
    "GTA6",
    "GTA VI",
    "Grand Theft Auto",
    "Vice City",
    "Leonida",
    "Lucia Camino",
    "Jason Duval",
    "Port Gellhorn"
]

STRONG = [
    "gta6",
    "gta 6",
    "gta vi",
    "grand theft auto",
    "grandtheftauto",
    "gtavi"
]

RELATED = [
    "vice city",
    "leonida",
    "lucia camino",
    "jason duval",
    "port gellhorn",
    "raul bautista"
]

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6RadarResearch/5.7.1",
    "Accept": "application/json"
})

_LOCK = threading.RLock()
_LAST_BACKUP = 0.0


# ============================================================
# HELPERS
# ============================================================

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value):
    result = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def be_time(value):
    if not value:
        return "-"
    return parse_time(value).astimezone(LOCAL).strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def number(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def zero(value):
    return number(value) or 0.0


def matches(text, patterns):
    text = str(text or "").lower()
    return any(
        re.search(
            r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
            text
        )
        for term in patterns
    )


def secret(key, default=""):
    if os.getenv(key):
        return os.environ[key]

    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, AttributeError, KeyError):
        return default


def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def select(sql, args=()):
    with db() as connection:
        return connection.execute(sql, args).fetchall()


# ============================================================
# SUPABASE STORAGE
# ============================================================

def storage_config():
    host = secret("SUPABASE_URL").rstrip("/")
    key = secret("SUPABASE_SECRET_KEY")

    bucket = secret(
        "SUPABASE_BACKUP_BUCKET",
        "gta6-private-backups"
    )

    object_name = secret(
        "SUPABASE_BACKUP_OBJECT",
        "radar/gta6_radar.db"
    ).lstrip("/")

    if not host or not key:
        return None

    if (
        not host.startswith("https://")
        or "/rest/v1" in host
        or not key.startswith("sb_secret_")
    ):
        raise RuntimeError(
            "Controleer je Supabase URL en Secret Key"
        )

    return host, key, bucket, object_name


def object_url(config, object_name):
    host, key, bucket, _ = config

    return (
        host
        + "/storage/v1/object/"
        + quote(bucket, safe="")
        + "/"
        + quote(object_name, safe="/")
    )


def get_object(config, object_name):
    response = requests.get(
        object_url(config, object_name),
        headers={"apikey": config[1]},
        timeout=40
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return response.content


def upload_object(config, object_name, data, overwrite=False):
    headers = {
        "apikey": config[1],
        "Content-Type": "application/octet-stream"
    }

    if overwrite:
        headers["x-upsert"] = "true"

    response = requests.post(
        object_url(config, object_name),
        headers=headers,
        data=data,
        timeout=60
    )

    response.raise_for_status()


def inspect_database(blob):
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        tmp.write(blob)
        tmp.flush()

        with sqlite3.connect(
            "file:" + tmp.name + "?mode=ro",
            uri=True
        ) as connection:

            result = connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]

            if result != "ok":
                raise sqlite3.DatabaseError(
                    "Database-integriteitscontrole mislukt"
                )

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table'"
                )
            }

            required = [
                "paper_v54",
                "paper_v542_b",
                "radar_v54",
                "v542_runs"
            ]

            if not set(required).issubset(tables):
                raise sqlite3.DatabaseError(
                    "Belangrijke radartabellen ontbreken"
                )

            stats = {}

            for table in required:
                stats[table] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]

            stats["last_scan"] = (
                connection.execute(
                    "SELECT MAX(started_at) FROM v542_runs"
                ).fetchone()[0] or ""
            )

            stats["last_trade_a"] = (
                connection.execute(
                    "SELECT MAX(opened_at) FROM paper_v54"
                ).fetchone()[0] or ""
            )

            stats["last_trade_b"] = (
                connection.execute(
                    "SELECT MAX(opened_at) FROM paper_v542_b"
                ).fetchone()[0] or ""
            )

            return stats


def database_snapshot():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        with sqlite3.connect(DB, timeout=30) as source:
            with sqlite3.connect(tmp.name) as destination:
                source.backup(destination)

        return Path(tmp.name).read_bytes()


def restore_database():
    """
    Herstel uitsluitend wanneer de lokale database ontbreekt.
    """

    if Path(DB).exists():
        return "Bestaande lokale database behouden"

    config = storage_config()

    if config is None:
        raise RuntimeError(
            "Geen lokale database en geen Supabase-configuratie"
        )

    blob = get_object(config, config[3])

    if blob is None:
        raise RuntimeError(
            "Geen bestaande Supabase-back-up gevonden"
        )

    stats = inspect_database(blob)

    Path(DB).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".db",
        dir=str(Path(DB).parent)
    ) as tmp:
        tmp.write(blob)
        temporary = tmp.name

    try:
        os.replace(temporary, DB)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return (
        "Supabase hersteld | "
        f"A: {stats['paper_v54']} | "
        f"B: {stats['paper_v542_b']} papertrades"
    )


def backup_remote(force=False):
    """
    Eerst een unieke back-upversie opslaan.
    Daarna pas de actieve back-up bijwerken.
    """

    global _LAST_BACKUP

    config = storage_config()

    if config is None:
        raise RuntimeError(
            "Supabase-back-up is niet ingesteld"
        )

    if not force:
        if time.monotonic() - _LAST_BACKUP < 120:
            return "Recent geback-upt"

    with _LOCK:

        if not Path(DB).exists():
            raise RuntimeError(
                "Lokale database ontbreekt: upload geblokkeerd"
            )

        blob = database_snapshot()
        local = inspect_database(blob)

        previous_blob = get_object(
            config,
            config[3]
        )

        if previous_blob is not None:
            previous = inspect_database(previous_blob)

            for table in [
                "paper_v54",
                "paper_v542_b",
                "radar_v54",
                "v542_runs"
            ]:
                if local[table] < previous[table]:
                    raise RuntimeError(
                        "BACK-UP GEBLOKKEERD: "
                        f"{table} lokaal={local[table]}, "
                        f"extern={previous[table]}"
                    )

            for field in [
                "last_scan",
                "last_trade_a",
                "last_trade_b"
            ]:
                if local[field] < previous[field]:
                    raise RuntimeError(
                        "BACK-UP GEBLOKKEERD: "
                        f"lokale geschiedenis ouder ({field})"
                    )

        prefix = (
            config[3].rsplit("/", 1)[0]
            if "/" in config[3]
            else "radar"
        )

        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")

        version = (
            f"{prefix}/versions/"
            f"{timestamp}_{uuid.uuid4().hex[:8]}.db"
        )

        # Versie eerst bewaren
        upload_object(
            config,
            version,
            blob,
            overwrite=False
        )

        # Daarna actieve back-up vervangen
        upload_object(
            config,
            config[3],
            blob,
            overwrite=True
        )

        _LAST_BACKUP = time.monotonic()

        return (
            f"BACKUP OK | {len(blob):,} bytes | {version}"
        )


try:
    BACKUP_BOOT = restore_database()
except (
    requests.RequestException,
    sqlite3.Error,
    OSError,
    ValueError,
    RuntimeError
) as exc:
    st.error(f"Veilig databaseherstel mislukt: {exc}")
    st.stop()


# ============================================================
# DATABASE INITIALISATIE
# ============================================================

def initialize():

    statements = [
        """
        CREATE TABLE IF NOT EXISTS discovered (
            address TEXT PRIMARY KEY,
            name TEXT,
            symbol TEXT,
            first_seen TEXT,
            last_seen TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS tracking_state (
            address TEXT PRIMARY KEY,
            first_seen TEXT,
            last_checked TEXT,
            last_found TEXT,
            missed_scans INTEGER DEFAULT 0,
            status TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS radar_v54 (
            address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT,
            symbol TEXT,
            pair_address TEXT,
            source TEXT,
            price REAL,
            liquidity REAL,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            age_hours REAL,
            narrative INTEGER,
            momentum INTEGER,
            evidence TEXT,
            phase TEXT,
            warnings TEXT,
            PRIMARY KEY(address, scanned_at)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS paper_v54 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT,
            pair_address TEXT,
            opened_at TEXT NOT NULL,
            entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL,
            amount_usd REAL NOT NULL,
            quantity REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL,
            status TEXT DEFAULT 'OPEN',
            closed_at TEXT,
            exit_market REAL,
            exit_effective REAL,
            pnl_usd REAL,
            exit_reason TEXT,
            last_mark REAL,
            last_mark_at TEXT,
            peak_mark REAL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS paper_v542_b (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT,
            pair_address TEXT,
            opened_at TEXT NOT NULL,
            entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL,
            amount_usd REAL NOT NULL,
            quantity REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL,
            status TEXT DEFAULT 'OPEN',
            closed_at TEXT,
            exit_market REAL,
            exit_effective REAL,
            pnl_usd REAL,
            exit_reason TEXT,
            last_mark REAL,
            last_mark_at TEXT,
            peak_mark REAL
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v541_config (
            name TEXT PRIMARY KEY,
            value TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v541_runs (
            scanned_at TEXT PRIMARY KEY,
            finished_at TEXT,
            fetched INTEGER,
            errors INTEGER,
            entries INTEGER,
            exits INTEGER,
            cause TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v541_decisions (
            scan_at TEXT,
            address TEXT,
            name TEXT,
            outcome TEXT,
            reason TEXT,
            price REAL,
            liquidity REAL,
            PRIMARY KEY(scan_at, address)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v542_decisions (
            scan_at TEXT NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            strategy TEXT NOT NULL,
            outcome TEXT,
            reason TEXT,
            price REAL,
            liquidity REAL,
            PRIMARY KEY(scan_at, address, strategy)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v542_runs (
            started_at TEXT PRIMARY KEY,
            finished_at TEXT,
            cause TEXT,
            tokens INTEGER,
            api_errors INTEGER,
            a_entries INTEGER,
            b_entries INTEGER,
            a_exits INTEGER,
            b_exits INTEGER
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS v57_monitor (
            checked_at TEXT PRIMARY KEY,
            positions INTEGER,
            pairs INTEGER,
            exits INTEGER,
            api_errors INTEGER,
            backup_status TEXT
        )
        """
    ]

    migrations = {
        "v542_runs": {
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "cause": "TEXT",
            "tokens": "INTEGER",
            "api_errors": "INTEGER",
            "a_entries": "INTEGER",
            "b_entries": "INTEGER",
            "a_exits": "INTEGER",
            "b_exits": "INTEGER"
        },
        "radar_v54": {
            "address": "TEXT",
            "scanned_at": "TEXT",
            "name": "TEXT",
            "symbol": "TEXT",
            "pair_address": "TEXT",
            "source": "TEXT",
            "price": "REAL",
            "liquidity": "REAL",
            "volume_m5": "REAL",
            "buys_m5": "INTEGER",
            "sells_m5": "INTEGER",
            "age_hours": "REAL",
            "narrative": "INTEGER",
            "momentum": "INTEGER",
            "evidence": "TEXT",
            "phase": "TEXT",
            "warnings": "TEXT"
        },
        "paper_v54": {
            "address": "TEXT",
            "name": "TEXT",
            "pair_address": "TEXT",
            "opened_at": "TEXT",
            "entry_market": "REAL",
            "entry_effective": "REAL",
            "amount_usd": "REAL",
            "quantity": "REAL",
            "fee_rate": "REAL",
            "slippage_rate": "REAL",
            "status": "TEXT",
            "closed_at": "TEXT",
            "exit_market": "REAL",
            "exit_effective": "REAL",
            "pnl_usd": "REAL",
            "exit_reason": "TEXT",
            "last_mark": "REAL",
            "last_mark_at": "TEXT",
            "peak_mark": "REAL"
        },
        "paper_v542_b": {
            "address": "TEXT",
            "name": "TEXT",
            "pair_address": "TEXT",
            "opened_at": "TEXT",
            "entry_market": "REAL",
            "entry_effective": "REAL",
            "amount_usd": "REAL",
            "quantity": "REAL",
            "fee_rate": "REAL",
            "slippage_rate": "REAL",
            "status": "TEXT",
            "closed_at": "TEXT",
            "exit_market": "REAL",
            "exit_effective": "REAL",
            "pnl_usd": "REAL",
            "exit_reason": "TEXT",
            "last_mark": "REAL",
            "last_mark_at": "TEXT",
            "peak_mark": "REAL"
        },
        "v542_decisions": {
            "scan_at": "TEXT",
            "address": "TEXT",
            "name": "TEXT",
            "strategy": "TEXT",
            "outcome": "TEXT",
            "reason": "TEXT",
            "price": "REAL",
            "liquidity": "REAL"
        }
    }

    with db() as connection:

        for statement in statements:
            connection.execute(statement)

        for table, columns in migrations.items():

            existing = {
                row[1]
                for row in connection.execute(
                    f'PRAGMA table_info("{table}")'
                )
            }

            for column, kind in columns.items():
                if column not in existing:
                    connection.execute(
                        f'ALTER TABLE "{table}" '
                        f'ADD COLUMN "{column}" {kind}'
                    )

        run_columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("v542_runs")'
            )
        }

        if "scan_at" in run_columns:
            connection.execute(
                "UPDATE v542_runs SET started_at=scan_at "
                "WHERE started_at IS NULL"
            )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v54_history "
            "ON radar_v54(address,scanned_at)"
        )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v54_paper "
            "ON paper_v54(status)"
        )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v542_paper "
            "ON paper_v542_b(status)"
        )


# ============================================================
# CONFIGURATIE
# ============================================================

def get_bool(key, default):
    rows = select(
        "SELECT value FROM v541_config WHERE name=?",
        (key,)
    )
    return rows[0][0] == "1" if rows else default


def set_bool(key, state):
    with db() as connection:
        connection.execute(
            """
            INSERT INTO v541_config(name,value)
            VALUES(?,?)
            ON CONFLICT(name)
            DO UPDATE SET value=excluded.value
            """,
            (key, "1" if state else "0")
        )


# ============================================================
# DEXSCREENER API
# ============================================================

def api_get(path, params=None):
    response = HTTP.get(
        API + path,
        params=params,
        timeout=20
    )

    response.raise_for_status()
    return response.json()


def discover():
    addresses = {}
    errors = []

    def add(address, source):
        if isinstance(address, str):
            if ADDRESS.fullmatch(address):
                addresses.setdefault(address, set()).add(source)

    for term in SEARCHES:
        try:
            payload = api_get(
                "/latest/dex/search",
                {"q": term}
            )

            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}
                text = (
                    str(base.get("name", ""))
                    + " "
                    + str(base.get("symbol", ""))
                )

                if matches(text, STRONG + RELATED):
                    add(base.get("address"), "DEX search")

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(f"Search {term}: {exc}")

    for path in [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]:
        try:
            payload = api_get(path)

            if not isinstance(payload, list):
                continue

            for item in payload:
                if item.get("chainId") != "solana":
                    continue

                links = " ".join(
                    str(link.get("url", ""))
                    for link in (item.get("links") or [])
                    if isinstance(link, dict)
                )

                description = (
                    str(item.get("description") or "")
                    + " "
                    + links
                )

                if matches(description, STRONG + RELATED):
                    add(
                        item.get("tokenAddress"),
                        "DEX profile"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(f"Profile {path}: {exc}")

    queries = [
        "SELECT address FROM paper_v54 WHERE status='OPEN'",
        "SELECT address FROM paper_v542_b WHERE status='OPEN'",
        "SELECT address FROM watchlist",
        "SELECT address FROM tracking_state LIMIT 200",
        "SELECT address FROM discovered LIMIT 200"
    ]

    for sql in queries:
        for (address,) in select(sql):
            add(address, "Gevolgd")

    protected = {
        row[0]
        for row in select(
            "SELECT address FROM watchlist"
        )
    }

    for table in ["paper_v54", "paper_v542_b"]:
        protected.update(
            row[0]
            for row in select(
                f"SELECT address FROM {table} WHERE status='OPEN'"
            )
        )

    recent = select(
        """
        SELECT r.address,r.scanned_at,r.volume_m5
        FROM radar_v54 r
        INNER JOIN (
            SELECT address,MAX(scanned_at) latest
            FROM radar_v54
            GROUP BY address
        ) x
        ON x.address=r.address
        AND x.latest=r.scanned_at
        """
    )

    inactive = set()

    for address, scanned, volume in recent:
        minutes = (
            datetime.now(timezone.utc)
            - parse_time(scanned)
        ).total_seconds() / 60

        if (
            zero(volume) < 25
            and minutes < 60
            and address not in protected
        ):
            inactive.add(address)

    filtered = {
        address: sources
        for address, sources in addresses.items()
        if address not in inactive
    }

    return (
        filtered,
        errors,
        len(addresses) - len(filtered)
    )


def market_pairs(addresses):
    results = {}
    fetched = set()
    errors = []

    for offset in range(0, len(addresses), 30):
        batch = addresses[offset:offset + 30]

        try:
            payload = api_get(
                "/tokens/v1/solana/" + ",".join(batch)
            )

            if not isinstance(payload, list):
                raise ValueError(
                    "Onverwacht marktdataformaat"
                )

            fetched.update(batch)

            for pair in payload:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address")

                if address not in batch:
                    continue

                current = results.get(address)

                new_liquidity = zero(
                    (pair.get("liquidity") or {}).get("usd")
                )

                old_liquidity = zero(
                    (current.get("liquidity") or {}).get("usd")
                ) if current else -1

                if new_liquidity > old_liquidity:
                    results[address] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Marktdata batch {offset // 30 + 1}: {exc}"
            )

    return results, fetched, errors


# ============================================================
# SIGNAALANALYSE
# ============================================================

def history(address, stamp):
    rows = select(
        """
        SELECT scanned_at,price,volume_m5,pair_address
        FROM radar_v54
        WHERE address=? AND scanned_at<?
        ORDER BY scanned_at DESC
        LIMIT 16
        """,
        (address, stamp)
    )

    return [
        (t, number(p), number(v), pair)
        for t, p, v, pair in rows
    ]


def signals(address, stamp, price, volume, pair):
    if price is None or price <= 0:
        return (
            0, False,
            "Prijs ontbreekt of is ongeldig",
            "ONVOLLEDIGE_DATA"
        )

    if volume is None or volume <= 0:
        return (
            0, False,
            "Geen geldig 5m-volume",
            "GEEN_HANDEL"
        )

    previous = [
        item
        for item in history(address, stamp)
        if (
            item[1] is not None
            and item[1] > 0
            and item[2] is not None
            and item[2] > 0
            and item[3] == pair
        )
    ]

    if len(previous) < 2:
        return (
            0, False,
            f"{len(previous) + 1}/3 metingen",
            "TE_WEINIG_METINGEN"
        )

    recent = next(
        (
            item
            for item in previous
            if 3.5 <= (
                parse_time(stamp)
                - parse_time(item[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )

    old = next(
        (
            item
            for item in previous
            if recent
            and 3.5 <= (
                parse_time(recent[0])
                - parse_time(item[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )

    if recent is None or old is None:
        return (
            0, False,
            "Geen drie geldige opeenvolgende meetpunten",
            "ONGELDIG_INTERVAL"
        )

    gain = price / old[1] - 1

    prices_rising = (
        old[1] < recent[1] < price
    )

    volume_rising = (
        old[2] < recent[2] < volume
    )

    signal_a = (
        prices_rising
        and gain >= 0.02
        and volume_rising
        and volume >= 250
    )

    signal_b = (
        gain >= 0.05
        and price > recent[1]
        and volume >= 250
    )

    score_a = (
        80 if signal_a
        else 30 if prices_rising or volume_rising
        else 0
    )

    explanation = (
        f"Koersverandering: {gain:+.1%}; "
        f"prijzen stijgend: {prices_rising}; "
        f"volume stijgend: {volume_rising}; "
        f"5m-volume: ${volume:,.0f}; "
        f"A: {signal_a}; B: {signal_b}"
    )

    return (
        score_a,
        signal_b,
        explanation,
        "GEMETEN"
    )


def analyze(address, pair, origins, stamp):
    base = pair.get("baseToken") or {}

    name = str(base.get("name") or "Onbekend")
    symbol = str(base.get("symbol") or "?")

    title = f"{name} {symbol}"

    narrative = (
        100 if matches(title, STRONG)
        else 65 if matches(title, RELATED)
        else 0
    )

    created = number(pair.get("pairCreatedAt"))

    age = (
        max(
            0,
            (
                parse_time(stamp).timestamp() * 1000
                - created
            ) / 3600000
        )
        if created
        else None
    )

    price = number(pair.get("priceUsd"))

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = number(
        (pair.get("volume") or {}).get("m5")
    )

    transactions = (
        (pair.get("txns") or {}).get("m5") or {}
    )

    buys = int(zero(transactions.get("buys")))
    sells = int(zero(transactions.get("sells")))

    pair_id = str(pair.get("pairAddress") or "")

    score_a, signal_b, evidence, diagnostic = signals(
        address,
        stamp,
        price,
        volume,
        pair_id
    )

    total_transactions = buys + sells

    average_trade = (
        volume / total_transactions
        if total_transactions and volume is not None
        else None
    )

    micro = (
        total_transactions >= 100
        and average_trade is not None
        and average_trade < 3
    )

    warnings = []

    if liquidity is None or liquidity < 10000:
        warnings.append("Lage of onbekende liquiditeit")

    if micro:
        warnings.append("Mogelijke microtransactie-bots")

    warnings.append(
        "Mint/freeze, houders en verkoopbaarheid niet geverifieerd"
    )

    return {
        "address": address,
        "name": name,
        "symbol": symbol,
        "pair": pair_id,
        "source": ", ".join(sorted(origins)),
        "narrative": narrative,
        "age": age,
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "micro": micro,
        "a_score": score_a,
        "b_signal": signal_b,
        "evidence": evidence,
        "diagnostic": diagnostic,
        "warnings": "; ".join(warnings)
    }


# ============================================================
# OBSERVATIES OPSLAAN
# ============================================================

def record_observations(records, stamp):
    with db() as connection:

        for item in records:

            connection.execute(
                """
                INSERT INTO discovered(
                    address,name,symbol,first_seen,last_seen
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(address)
                DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
                """,
                (
                    item["address"],
                    item["name"],
                    item["symbol"],
                    stamp,
                    stamp
                )
            )

            connection.execute(
                """
                INSERT INTO tracking_state(
                    address,first_seen,last_checked,
                    last_found,missed_scans,status
                )
                VALUES(?,?,?,?,0,'ACTIEF')
                ON CONFLICT(address)
                DO UPDATE SET
                    last_checked=excluded.last_checked,
                    last_found=excluded.last_found,
                    missed_scans=0,
                    status='ACTIEF'
                """,
                (
                    item["address"],
                    stamp,
                    stamp,
                    stamp
                )
            )

            phase = (
                "MOMENTUM TER CONTROLE"
                if item["a_score"] == 80
                else "PRE-TREND KANDIDAAT"
                if item["age"] is not None
                and item["age"] < 24
                else "MONITOREN"
            )

            connection.execute(
                """
                INSERT OR REPLACE INTO radar_v54(
                    address,scanned_at,name,symbol,
                    pair_address,source,price,liquidity,
                    volume_m5,buys_m5,sells_m5,age_hours,
                    narrative,momentum,evidence,phase,warnings
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["address"],
                    stamp,
                    item["name"],
                    item["symbol"],
                    item["pair"],
                    item["source"],
                    item["price"],
                    item["liquidity"],
                    item["volume"],
                    item["buys"],
                    item["sells"],
                    item["age"],
                    item["narrative"],
                    item["a_score"],
                    item["evidence"],
                    phase,
                    item["warnings"]
                )
            )


# ============================================================
# PAPER STRATEGIE A EN B
# ============================================================

def decide(item, strategy, config, used):

    if item["address"] in used:
        return (
            "GEEN_INSTAP",
            "Token eerder verhandeld in deze strategie"
        )

    if item["narrative"] < 65:
        return (
            "GEEN_INSTAP",
            "Geen duidelijke GTA 6-narrative"
        )

    if item["diagnostic"] != "GEMETEN":
        return (
            "GEEN_INSTAP",
            item["diagnostic"]
        )

    if strategy == "A" and item["a_score"] != 80:
        return (
            "GEEN_INSTAP",
            "Strategie A-signaal niet bevestigd"
        )

    if strategy == "B" and not item["b_signal"]:
        return (
            "GEEN_INSTAP",
            "Strategie B-signaal niet bevestigd"
        )

    if item["price"] is None or item["price"] <= 0:
        return "GEBLOKKEERD", "Prijs ongeldig"

    if (
        item["liquidity"] is None
        or item["liquidity"] < config["min_liq"]
    ):
        return "GEBLOKKEERD", "Onvoldoende liquiditeit"

    if item["age"] is None or item["age"] > 24:
        return (
            "GEBLOKKEERD",
            "Handelspaar ouder dan 24 uur"
        )

    if item["volume"] is None or item["volume"] < 250:
        return "GEBLOKKEERD", "Volume te laag"

    if item["buys"] <= item["sells"]:
        return (
            "GEBLOKKEERD",
            "Onvoldoende koopdruk"
        )

    if strategy == "A" and item["micro"]:
        return (
            "GEBLOKKEERD",
            "Mogelijke microtransactie-bots"
        )

    if strategy == "B" and item["micro"]:
        return (
            "KANDIDAAT",
            "Experimenteel signaal met microtransacties"
        )

    return (
        "KANDIDAAT",
        "Signaal bevestigd; contractrisico niet gecontroleerd"
    )


def paper_run(records, stamp, config, active_a, active_b):

    by_address = {
        item["address"]: item
        for item in records
    }

    results = {
        "A": [0, 0],
        "B": [0, 0]
    }

    ledgers = {
        "A": "paper_v54",
        "B": "paper_v542_b"
    }

    for strategy, table in ledgers.items():

        enabled = (
            active_a if strategy == "A"
            else active_b
        )

        with db() as connection:

            open_trades = connection.execute(
                f"""
                SELECT id,address,opened_at,entry_market,
                       quantity,amount_usd,fee_rate,
                       slippage_rate,pair_address
                FROM {table}
                WHERE status='OPEN'
                """
            ).fetchall()

            used = {
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT address FROM {table}"
                )
            }

            for trade in open_trades:

                (
                    trade_id,
                    address,
                    opened,
                    entry,
                    quantity,
                    amount,
                    fee,
                    slip,
                    original_pair
                ) = trade

                item = by_address.get(address)

                if not item:
                    continue

                price = item["price"]

                if price is None or price <= 0:
                    continue

                if (
                    original_pair
                    and item["pair"] != original_pair
                ):
                    connection.execute(
                        f"""
                        UPDATE {table}
                        SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                        WHERE id=? AND status='OPEN'
                        """,
                        (trade_id,)
                    )
                    continue

                connection.execute(
                    f"""
                    UPDATE {table}
                    SET last_mark=?,
                        last_mark_at=?,
                        peak_mark=MAX(COALESCE(peak_mark,0),?)
                    WHERE id=? AND status='OPEN'
                    """,
                    (price, stamp, price, trade_id)
                )

                change = price / entry - 1

                hours = (
                    parse_time(stamp)
                    - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= config["take"]
                    else "STOP_LOSS"
                    if change <= -config["stop"]
                    else "TIME_EXIT"
                    if hours >= config["max_hours"]
                    else None
                )

                if reason is None:
                    continue

                if (
                    item["liquidity"] is None
                    or item["liquidity"] < config["min_liq"]
                ):
                    connection.execute(
                        f"""
                        UPDATE {table}
                        SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                        WHERE id=? AND status='OPEN'
                        """,
                        (trade_id,)
                    )
                    continue

                effective_exit = price * (1 - slip)

                pnl = (
                    quantity * effective_exit * (1 - fee)
                    - amount
                )

                cursor = connection.execute(
                    f"""
                    UPDATE {table}
                    SET status='CLOSED',
                        closed_at=?,
                        exit_market=?,
                        exit_effective=?,
                        pnl_usd=?,
                        exit_reason=?
                    WHERE id=? AND status='OPEN'
                    """,
                    (
                        stamp,
                        price,
                        effective_exit,
                        pnl,
                        reason,
                        trade_id
                    )
                )

                results[strategy][1] += cursor.rowcount

            current_open = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {table}
                WHERE status='OPEN'
                """
            ).fetchone()[0]

            for item in records:

                outcome, reason = decide(
                    item,
                    strategy,
                    config,
                    used
                )

                if outcome == "KANDIDAAT" and not enabled:
                    outcome = "UITGESCHAKELD"
                    reason = "Paperstrategie staat uit"

                elif (
                    outcome == "KANDIDAAT"
                    and current_open >= config["max_positions"]
                ):
                    outcome = "GEBLOKKEERD"
                    reason = "Maximaal aantal open posities"

                elif outcome == "KANDIDAAT":

                    effective_entry = (
                        item["price"] * (1 + config["slip"])
                    )

                    quantity = (
                        config["amount"] * (1 - config["fee"])
                        / effective_entry
                    )

                    connection.execute(
                        f"""
                        INSERT INTO {table}(
                            address,name,pair_address,opened_at,
                            entry_market,entry_effective,
                            amount_usd,quantity,
                            fee_rate,slippage_rate,
                            status,last_mark,last_mark_at,peak_mark
                        )
                        VALUES(
                            ?,?,?,?,?,?,?,?,?,?,
                            'OPEN',?,?,?
                        )
                        """,
                        (
                            item["address"],
                            item["name"],
                            item["pair"],
                            stamp,
                            item["price"],
                            effective_entry,
                            config["amount"],
                            quantity,
                            config["fee"],
                            config["slip"],
                            item["price"],
                            stamp,
                            item["price"]
                        )
                    )

                    used.add(item["address"])
                    current_open += 1

                    results[strategy][0] += 1
                    outcome = "PAPER_ENTRY"
                    reason = "Virtuele instap geregistreerd"

                connection.execute(
                    """
                    INSERT OR REPLACE INTO v542_decisions(
                        scan_at,address,name,strategy,
                        outcome,reason,price,liquidity
                    )
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        stamp,
                        item["address"],
                        item["name"],
                        strategy,
                        outcome,
                        reason,
                        item["price"],
                        item["liquidity"]
                    )
                )

    return results


# ============================================================
# HOOFDSCAN
# ============================================================

def scan(trigger, config, active_a, active_b):
    with _LOCK:
        return scan_locked(
            trigger,
            config,
            active_a,
            active_b
        )


def scan_locked(trigger, config, active_a, active_b):

    start = utcnow()

    addresses, errors, cooled = discover()

    pairs, fetched, fetch_errors = market_pairs(
        list(addresses)
    )

    errors.extend(fetch_errors)

    observed = []

    open_addresses = set()

    for table in ["paper_v54", "paper_v542_b"]:
        open_addresses.update(
            row[0]
            for row in select(
                f"""
                SELECT address
                FROM {table}
                WHERE status='OPEN'
                """
            )
        )

    for address, pair in pairs.items():

        try:
            item = analyze(
                address,
                pair,
                addresses[address],
                start
            )

            if (
                item["narrative"] >= 65
                or address in open_addresses
            ):
                observed.append(item)

        except (
            ValueError,
            KeyError,
            TypeError
        ) as exc:
            errors.append(
                f"Analyse {address[:10]}: {exc}"
            )

    record_observations(observed, start)

    results = paper_run(
        observed,
        start,
        config,
        active_a,
        active_b
    )

    end = utcnow()

    with db() as connection:
        connection.execute(
            """
            INSERT INTO v542_runs(
                started_at,finished_at,cause,
                tokens,api_errors,a_entries,b_entries,
                a_exits,b_exits
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                start,
                end,
                trigger,
                len(observed),
                len(errors),
                results["A"][0],
                results["B"][0],
                results["A"][1],
                results["B"][1]
            )
        )

    try:
        backup_remote(force=True)
    except (
        requests.RequestException,
        OSError,
        sqlite3.Error,
        RuntimeError,
        ValueError
    ) as exc:
        errors.append(
            "Externe back-up mislukt: " + str(exc)
        )

    return len(observed), results, errors, cooled


# ============================================================
# PAPERTRADE-MONITOR — ELKE 15 SECONDEN
# ============================================================

def monitor_positions(config):

    if not _LOCK.acquire(blocking=False):
        return None

    try:
        opens = []

        for table in ["paper_v54", "paper_v542_b"]:

            rows = select(
                f"""
                SELECT id,address,pair_address,opened_at,
                       entry_market,quantity,amount_usd,
                       fee_rate,slippage_rate
                FROM {table}
                WHERE status='OPEN'
                """
            )

            for trade in rows:
                opens.append((table, trade))

        if not opens:
            return {
                "open": 0,
                "checked": 0,
                "exits": 0,
                "errors": [],
                "backup": "Geen open posities"
            }

        addresses = sorted({
            trade[1]
            for _, trade in opens
        })

        pairs, fetched, errors = market_pairs(addresses)

        stamp = utcnow()
        exits = 0

        with db() as connection:

            for table, trade in opens:

                (
                    trade_id,
                    address,
                    original_pair,
                    opened,
                    entry,
                    quantity,
                    amount,
                    fee,
                    slip
                ) = trade

                pair = pairs.get(address)

                if pair is None:
                    continue

                pair_id = str(
                    pair.get("pairAddress") or ""
                )

                if (
                    original_pair
                    and pair_id != original_pair
                ):
                    connection.execute(
                        f"""
                        UPDATE {table}
                        SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                        WHERE id=? AND status='OPEN'
                        """,
                        (trade_id,)
                    )
                    continue

                price = number(pair.get("priceUsd"))

                liquidity = number(
                    (pair.get("liquidity") or {}).get("usd")
                )

                if price is None or price <= 0:
                    continue

                connection.execute(
                    f"""
                    UPDATE {table}
                    SET last_mark=?,
                        last_mark_at=?,
                        peak_mark=MAX(COALESCE(peak_mark,0),?)
                    WHERE id=? AND status='OPEN'
                    """,
                    (
                        price,
                        stamp,
                        price,
                        trade_id
                    )
                )

                change = price / entry - 1

                held = (
                    parse_time(stamp)
                    - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= config["take"]
                    else "STOP_LOSS"
                    if change <= -config["stop"]
                    else "TIME_EXIT"
                    if held >= config["max_hours"]
                    else None
                )

                if reason is None:
                    continue

                if (
                    liquidity is None
                    or liquidity < config["min_liq"]
                ):
                    connection.execute(
                        f"""
                        UPDATE {table}
                        SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                        WHERE id=? AND status='OPEN'
                        """,
                        (trade_id,)
                    )
                    continue

                effective_exit = (
                    price * (1 - slip)
                )

                pnl = (
                    quantity * effective_exit * (1 - fee)
                    - amount
                )

                cursor = connection.execute(
                    f"""
                    UPDATE {table}
                    SET status='CLOSED',
                        closed_at=?,
                        exit_market=?,
                        exit_effective=?,
                        pnl_usd=?,
                        exit_reason=?
                    WHERE id=? AND status='OPEN'
                    """,
                    (
                        stamp,
                        price,
                        effective_exit,
                        pnl,
                        reason,
                        trade_id
                    )
                )

                exits += cursor.rowcount

            connection.execute(
                """
                INSERT OR REPLACE INTO v57_monitor(
                    checked_at,positions,pairs,
                    exits,api_errors,backup_status
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    stamp,
                    len(opens),
                    len(pairs),
                    exits,
                    len(errors),
                    "PENDING"
                )
            )

        try:
            status = backup_remote(
                force=exits > 0
            )
        except (
            requests.RequestException,
            OSError,
            sqlite3.Error,
            RuntimeError,
            ValueError
        ) as exc:
            status = "BACKUP ERROR: " + str(exc)
            errors.append(status)

        with db() as connection:
            connection.execute(
                """
                UPDATE v57_monitor
                SET backup_status=?
                WHERE checked_at=?
                """,
                (status, stamp)
            )

        return {
            "open": len(opens),
            "checked": len(pairs),
            "exits": exits,
            "errors": errors,
            "backup": status
        }

    finally:
        _LOCK.release()


# ============================================================
# DATAFRAMES VOOR DASHBOARD
# ============================================================

def latest_tokens():

    rows = select(
        """
        SELECT started_at
        FROM v542_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    if not rows:
        return pd.DataFrame()

    stamp = rows[0][0]

    data = select(
        """
        SELECT name,symbol,address,pair_address,
               age_hours,price,liquidity,volume_m5,
               buys_m5,sells_m5,momentum,evidence,warnings
        FROM radar_v54
        WHERE scanned_at=?
        """,
        (stamp,)
    )

    return pd.DataFrame(
        data,
        columns=[
            "Naam",
            "Symbool",
            "Adres",
            "Pair",
            "Leeftijd uur",
            "Prijs $",
            "Liquiditeit $",
            "5m-volume $",
            "Buys",
            "Sells",
            "A momentum",
            "Trend",
            "Waarschuwingen"
        ]
    )


def ledger_view(table):

    rows = select(
        f"""
        SELECT id,name,address,opened_at,
               amount_usd,entry_market,last_mark,
               status,pnl_usd,exit_reason
        FROM {table}
        ORDER BY id DESC
        LIMIT 300
        """
    )

    frame = pd.DataFrame(
        rows,
        columns=[
            "ID",
            "Naam",
            "Adres",
            "Open UTC",
            "Inzet $",
            "Instap $",
            "Laatste prijs $",
            "Status",
            "Gerealiseerd P/L $",
            "Exitreden"
        ]
    )

    if not frame.empty:
        frame["Geopend België"] = (
            frame["Open UTC"].apply(be_time)
        )

        frame = frame.drop(
            columns=["Open UTC"]
        )

    return frame


def paper_risk_frame(table):

    rows = select(
        f"""
        SELECT id,name,address,status,amount_usd,
               quantity,fee_rate,slippage_rate,
               entry_market,opened_at,pair_address,
               last_mark,last_mark_at,exit_reason
        FROM {table}
        WHERE status='OPEN'
        ORDER BY id DESC
        """
    )

    results = []

    for row in rows:

        (
            trade_id,
            name,
            address,
            status,
            stake,
            quantity,
            fee,
            slip,
            entry,
            opened,
            original_pair,
            mark,
            mark_at,
            reason
        ) = row

        latest = select(
            """
            SELECT scanned_at,price,liquidity,pair_address
            FROM radar_v54
            WHERE address=?
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            (address,)
        )

        current = latest[0] if latest else None

        pair_changed = bool(
            current
            and original_pair
            and current[3] != original_pair
        )

        price = (
            current[1]
            if current and not pair_changed
            else None
        )

        liquidity = (
            current[2]
            if current
            else None
        )

        sale = (
            quantity
            * price
            * (1 - zero(slip))
            * (1 - zero(fee))
            if price and quantity
            else None
        )

        pnl = (
            sale - stake
            if sale is not None
            else None
        )

        net_pct = (
            100 * pnl / stake
            if pnl is not None and stake
            else None
        )

        warning = (
            "PAIR GEWIJZIGD"
            if pair_changed
            else "GEEN ACTUELE PRIJS"
            if price is None
            else "LAGE LIQUIDITEIT"
            if liquidity is None or liquidity < 10000
            else "NETTO <= -15%"
            if net_pct is not None and net_pct <= -15
            else "NETTO <= -10%"
            if net_pct is not None and net_pct <= -10
            else "Verkoopbaarheid niet bevestigd"
        )

        results.append({
            "ID": trade_id,
            "Naam": name,
            "Adres": address,
            "Marktprijs": price,
            "Liquiditeit": liquidity,
            "Netto mark P/L %": net_pct,
            "Netto mark P/L $": pnl,
            "Waarschuwing": warning,
            "Exit_status": reason or "OPEN"
        })

    return pd.DataFrame(results)


# ============================================================
# LEARNING ENGINE STATUS
# ============================================================

def learning_panel():

    st.subheader("🧠 Learning Engine")

    st.caption(
        "De GitHub Learning Collector is een aparte database. "
        "Upload hier desgewenst zijn exportbestanden."
    )

    uploaded_json = st.file_uploader(
        "Upload data/latest_scan.json",
        type=["json"],
        key="learning_json"
    )

    if uploaded_json is not None:
        try:
            payload = json.load(uploaded_json)

            if not isinstance(payload, dict):
                raise ValueError("JSON-object verwacht")

            columns = st.columns(4)

            keys = [
                "discovered",
                "recorded",
                "total_snapshots",
                "total_labels"
            ]

            labels = [
                "Ontdekt",
                "Nieuwe snapshots",
                "Totaal snapshots",
                "Leerlabels"
            ]

            for col, key, label in zip(
                columns,
                keys,
                labels
            ):
                col.metric(
                    label,
                    payload.get(key, "-")
                )

            st.write(
                "Laatste run:",
                payload.get("finished_at_utc", "-")
            )

        except (
            ValueError,
            json.JSONDecodeError,
            UnicodeError
        ) as exc:
            st.error(f"Learning JSON ongeldig: {exc}")

    uploaded_db = st.file_uploader(
        "Upload data/learning.db",
        type=["db"],
        key="learning_db"
    )

    if uploaded_db is None:
        return

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".db"
        ) as tmp:

            tmp.write(uploaded_db.getvalue())
            tmp.flush()

            with sqlite3.connect(
                "file:" + tmp.name + "?mode=ro",
                uri=True
            ) as connection:

                snapshots = connection.execute(
                    "SELECT COUNT(*) FROM snapshots"
                ).fetchone()[0]

                outcomes = connection.execute(
                    "SELECT COUNT(*) FROM outcomes"
                ).fetchone()[0]

                c1, c2 = st.columns(2)

                c1.metric(
                    "Historische snapshots",
                    snapshots
                )

                c2.metric(
                    "Uitkomstlabels",
                    outcomes
                )

                latest = pd.read_sql_query(
                    """
                    SELECT name,token_address,pair_address,
                           scanned_at,price_usd,
                           liquidity_usd,volume_m5
                    FROM snapshots
                    ORDER BY scanned_at DESC
                    LIMIT 150
                    """,
                    connection
                )

                st.dataframe(
                    latest,
                    hide_index=True,
                    use_container_width=True
                )

    except (
        sqlite3.Error,
        ValueError
    ) as exc:
        st.error(
            f"Learning-database kon niet worden gelezen: {exc}"
        )


# ============================================================
# STREAMLIT APP INITIALISEREN
# ============================================================

initialize()

st.title("🎮 GTA 6 Radar V5.7.1")

st.caption(
    "Solana scanner · Strategie A/B · "
    "15-secondenmonitor · Supabase versieback-ups"
)

st.warning(
    "Dit is uitsluitend paper trading. "
    "Er worden geen echte transacties uitgevoerd. "
    "Prijsmetingen garanderen geen uitvoerbare verkoop."
)


# ============================================================
# SESSION STATE
# ============================================================

if "v542_auto" not in st.session_state:
    st.session_state.v542_auto = get_bool(
        "auto_scan",
        True
    )

if "v542_a" not in st.session_state:
    st.session_state.v542_a = get_bool(
        "paper",
        False
    )

if "v542_b" not in st.session_state:
    st.session_state.v542_b = get_bool(
        "paper_b",
        False
    )


def change_auto():
    set_bool(
        "auto_scan",
        st.session_state.v542_auto
    )


def change_a():
    set_bool(
        "paper",
        st.session_state.v542_a
    )


def change_b():
    set_bool(
        "paper_b",
        st.session_state.v542_b
    )


# ============================================================
# SIDEBAR — BACK-UP
# ============================================================

if storage_config():
    st.sidebar.success(
        "Supabase-configuratie aanwezig"
    )
else:
    st.sidebar.error(
        "Supabase-back-up niet geconfigureerd"
    )

st.sidebar.caption(BACKUP_BOOT)

last_backup = select(
    """
    SELECT checked_at,backup_status
    FROM v57_monitor
    WHERE backup_status LIKE 'BACKUP OK%'
    ORDER BY checked_at DESC
    LIMIT 1
    """
)

if last_backup:
    st.sidebar.caption(
        "Laatste geslaagde monitorback-up: "
        + be_time(last_backup[0][0])
    )
else:
    st.sidebar.caption(
        "Nog geen succesvolle monitorback-up geregistreerd"
    )


# ============================================================
# SIDEBAR — SCANNER
# ============================================================

st.sidebar.header("⚙️ Scannerinstellingen")

st.sidebar.toggle(
    "Automatisch scannen",
    key="v542_auto",
    on_change=change_auto
)

st.sidebar.toggle(
    "Strategie A: paperinstappen",
    key="v542_a",
    on_change=change_a
)

st.sidebar.toggle(
    "Strategie B: paperinstappen",
    key="v542_b",
    on_change=change_b
)

interval = st.sidebar.selectbox(
    "Scaninterval (minuten)",
    [5, 10, 15],
    index=0
)

minimum_liq = st.sidebar.number_input(
    "Minimale liquiditeit ($)",
    min_value=1000.0,
    max_value=1000000.0,
    value=10000.0,
    step=1000.0
)

amount = st.sidebar.number_input(
    "Virtuele inzet per positie ($)",
    min_value=5.0,
    max_value=1000.0,
    value=25.0,
    step=5.0
)

max_positions = st.sidebar.number_input(
    "Maximaal open posities per strategie",
    min_value=1,
    max_value=20,
    value=3
)

fee_pct = st.sidebar.number_input(
    "Fee per zijde (%)",
    min_value=0.0,
    max_value=10.0,
    value=1.0,
    step=0.25
)

slip_pct = st.sidebar.number_input(
    "Slippage per zijde (%)",
    min_value=0.0,
    max_value=30.0,
    value=2.0,
    step=0.5
)

take_pct = st.sidebar.number_input(
    "Take profit (%)",
    min_value=5,
    max_value=500,
    value=30,
    step=5
)

stop_pct = st.sidebar.number_input(
    "Stop loss (%)",
    min_value=5,
    max_value=95,
    value=15,
    step=5
)

max_hours = st.sidebar.number_input(
    "Maximale looptijd (uren)",
    min_value=1,
    max_value=72,
    value=4
)

settings = {
    "min_liq": minimum_liq,
    "amount": amount,
    "max_positions": max_positions,
    "fee": fee_pct / 100,
    "slip": slip_pct / 100,
    "take": take_pct / 100,
    "stop": stop_pct / 100,
    "max_hours": max_hours
}


# ============================================================
# HOOFDDASHBOARD
# ============================================================

@st.fragment(run_every="60s")
def dashboard():

    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    last = select(
        """
        SELECT started_at,finished_at,cause
        FROM v542_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    last_end = (
        parse_time(last[0][1])
        if last and last[0][1]
        else None
    )

    now = datetime.now(timezone.utc)

    next_scan = (
        last_end + timedelta(minutes=interval)
        if last_end
        else now
    )

    st.subheader("🕒 Scanplanning")

    st.write(
        "Automatisch scannen:",
        "AAN" if st.session_state.v542_auto else "UIT"
    )

    st.write(
        "Laatste scan:",
        be_time(last[0][1]) if last else "-"
    )

    st.write(
        "Volgende scan vanaf:",
        be_time(next_scan.isoformat())
        if st.session_state.v542_auto
        else "-"
    )

    due = (
        st.session_state.v542_auto
        and now >= next_scan
    )

    if manual or due:
        try:
            with st.spinner(
                "GTA 6-tokens worden gescand..."
            ):
                count, results, errors, cooled = scan(
                    "MANUEEL" if manual else "AUTOMATISCH",
                    settings,
                    st.session_state.v542_a,
                    st.session_state.v542_b
                )

            st.success(
                f"Scan voltooid: {count} tokens | "
                f"{cooled} inactief overgeslagen | "
                f"A: {results['A'][0]} instappen | "
                f"B: {results['B'][0]} instappen"
            )

            if errors:
                with st.expander(
                    f"{len(errors)} scanmeldingen"
                ):
                    st.write(errors[:35])

        except (
            sqlite3.Error,
            requests.RequestException,
            ValueError,
            TypeError,
            RuntimeError
        ) as exc:
            st.error(f"Scanfout: {exc}")

    tokens = latest_tokens()

    a_open = select(
        """
        SELECT COUNT(*)
        FROM paper_v54
        WHERE status='OPEN'
        """
    )[0][0]

    b_open = select(
        """
        SELECT COUNT(*)
        FROM paper_v542_b
        WHERE status='OPEN'
        """
    )[0][0]

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Tokens laatste scan",
        len(tokens)
    )

    c2.metric(
        "A-signalen",
        int((tokens["A momentum"] == 80).sum())
        if not tokens.empty
        else 0
    )

    c3.metric(
        "A open posities",
        a_open
    )

    c4.metric(
        "B open posities",
        b_open
    )

    # --------------------------------------------------------
    # TOKENS
    # --------------------------------------------------------

    st.subheader("📡 Laatste tokenmetingen")

    if tokens.empty:
        st.info(
            "Nog geen tokenmetingen beschikbaar."
        )

    else:
        tokens = tokens.sort_values(
            ["5m-volume $", "A momentum"],
            ascending=False,
            na_position="last"
        )

        st.dataframe(
            tokens,
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            "📥 Download tokenmetingen",
            tokens.to_csv(index=False).encode("utf-8"),
            "gta6_tokens.csv",
            "text/csv"
        )

        choices = {
            f"{row['Naam']} · {row['Adres'][:12]}":
                row["Adres"]
            for _, row in tokens.iterrows()
        }

        chosen = st.selectbox(
            "Bekijk tokenhistoriek",
            list(choices)
        )

        address = choices[chosen]

        st.code(
            address,
            language="text"
        )

        if st.button("⭐ Toevoegen aan watchlist"):
            with db() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO watchlist(
                        address,name,added_at
                    )
                    VALUES(?,?,?)
                    """,
                    (
                        address,
                        tokens[
                            tokens["Adres"] == address
                        ].iloc[0]["Naam"],
                        utcnow()
                    )
                )

            st.success("Toegevoegd aan watchlist")

        history_rows = select(
            """
            SELECT scanned_at,price,volume_m5
            FROM radar_v54
            WHERE address=?
            ORDER BY scanned_at DESC
            LIMIT 120
            """,
            (address,)
        )

        if len(history_rows) >= 2:

            history_frame = pd.DataFrame(
                history_rows,
                columns=[
                    "Tijd",
                    "Prijs $",
                    "Volume $"
                ]
            )

            history_frame["Tijd"] = pd.to_datetime(
                history_frame["Tijd"],
                utc=True
            ).dt.tz_convert("Europe/Brussels")

            history_frame = (
                history_frame
                .sort_values("Tijd")
                .set_index("Tijd")
            )

            st.line_chart(
                history_frame[["Prijs $"]]
            )

            st.line_chart(
                history_frame[["Volume $"]]
            )

    # --------------------------------------------------------
    # STRATEGIEBESLISSINGEN
    # --------------------------------------------------------

    st.subheader("🔎 Strategie A versus Strategie B")

    newest = select(
        """
        SELECT started_at
        FROM v542_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    if newest:

        decisions = select(
            """
            SELECT name,address,strategy,outcome,
                   reason,price,liquidity
            FROM v542_decisions
            WHERE scan_at=?
            ORDER BY strategy,outcome,name
            """,
            (newest[0][0],)
        )

        decision_frame = pd.DataFrame(
            decisions,
            columns=[
                "Naam",
                "Adres",
                "Strategie",
                "Uitkomst",
                "Reden",
                "Prijs $",
                "Liquiditeit $"
            ]
        )

        strategy_filter = st.radio(
            "Toon strategie",
            ["Allebei", "A", "B"],
            horizontal=True
        )

        if strategy_filter != "Allebei":
            decision_frame = decision_frame[
                decision_frame["Strategie"]
                == strategy_filter
            ]

        st.dataframe(
            decision_frame,
            hide_index=True,
            use_container_width=True
        )

    # --------------------------------------------------------
    # PAPERPORTFOLIOS
    # --------------------------------------------------------

    st.subheader("🧪 Paperportfolio's")

    tab_a, tab_b = st.tabs([
        "Strategie A",
        "Strategie B"
    ])

    for tab, table in [
        (tab_a, "paper_v54"),
        (tab_b, "paper_v542_b")
    ]:

        with tab:

            trades = ledger_view(table)

            if trades.empty:
                st.info(
                    "Nog geen papertrades in deze strategie."
                )

            else:
                closed = trades[
                    trades["Status"] == "CLOSED"
                ]

                realized = closed[
                    "Gerealiseerd P/L $"
                ].sum()

                wins = (
                    (
                        closed["Gerealiseerd P/L $"] > 0
                    ).mean() * 100
                    if not closed.empty
                    else 0
                )

                p1, p2, p3 = st.columns(3)

                p1.metric(
                    "Totaal papertrades",
                    len(trades)
                )

                p2.metric(
                    "Gerealiseerd P/L",
                    f"${realized:,.2f}"
                )

                p3.metric(
                    "Winrate gesloten trades",
                    f"{wins:.1f}%"
                    if not closed.empty
                    else "-"
                )

                st.dataframe(
                    trades,
                    hide_index=True,
                    use_container_width=True
                )

                st.download_button(
                    f"📥 Download {table}",
                    trades.to_csv(index=False).encode(
                        "utf-8"
                    ),
                    f"{table}.csv",
                    "text/csv"
                )

    # --------------------------------------------------------
    # RISICOBEWAKING
    # --------------------------------------------------------

    st.subheader(
        "🚨 Risicobewaking open paperposities"
    )

    st.caption(
        "Indicatieve marktwaardering. "
        "Geen gegarandeerde verkoopprijs."
    )

    for name, table in [
        ("Strategie A", "paper_v54"),
        ("Strategie B", "paper_v542_b")
    ]:

        risk = paper_risk_frame(table)

        if not risk.empty:
            st.write(f"**{name}**")

            st.dataframe(
                risk,
                hide_index=True,
                use_container_width=True
            )

    # --------------------------------------------------------
    # LEARNING ENGINE
    # --------------------------------------------------------

    with st.expander(
        "🧠 Learning Engine onderzoeken"
    ):
        learning_panel()

    # --------------------------------------------------------
    # SCANLOG
    # --------------------------------------------------------

    st.subheader("🕒 Scanlog")

    logs = select(
        """
        SELECT started_at,finished_at,cause,
               tokens,api_errors,
               a_entries,b_entries,
               a_exits,b_exits
        FROM v542_runs
        ORDER BY started_at DESC
        LIMIT 40
        """
    )

    if logs:

        log_frame = pd.DataFrame(
            logs,
            columns=[
                "Begin UTC",
                "Einde UTC",
                "Trigger",
                "Tokens",
                "API-meldingen",
                "A instappen",
                "B instappen",
                "A exits",
                "B exits"
            ]
        )

        log_frame["Begin België"] = (
            log_frame["Begin UTC"].apply(be_time)
        )

        log_frame["Einde België"] = (
            log_frame["Einde UTC"].apply(be_time)
        )

        st.dataframe(
            log_frame.drop(
                columns=[
                    "Begin UTC",
                    "Einde UTC"
                ]
            ),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# 15-SECONDEN PAPERTRADE-MONITOR
# ============================================================

@st.fragment(run_every="15s")
def paper_monitor_fragment():

    st.subheader(
        "⚡ Papertrade-monitor — 15 seconden"
    )

    st.caption(
        "Controleert open paperposities zolang "
        "de Streamlit-sessie actief is."
    )

    result = monitor_positions(settings)

    if result is None:
        st.info(
            "Hoofdscan actief. Monitor hervat daarna."
        )

    elif result["open"] == 0:
        st.info(
            "Geen open paperposities."
        )

    else:
        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Open posities",
            result["open"]
        )

        c2.metric(
            "Tokens gecontroleerd",
            result["checked"]
        )

        c3.metric(
            "Virtuele exits",
            result["exits"]
        )

        if result["errors"]:
            st.warning(
                " | ".join(result["errors"][:3])
            )

    monitor_rows = select(
        """
        SELECT checked_at,positions,pairs,
               exits,backup_status
        FROM v57_monitor
        ORDER BY checked_at DESC
        LIMIT 5
        """
    )

    if monitor_rows:

        monitor_frame = pd.DataFrame(
            monitor_rows,
            columns=[
                "UTC",
                "Posities",
                "Pairs",
                "Exits",
                "Back-up"
            ]
        )

        st.dataframe(
            monitor_frame,
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# APP START
# ============================================================

paper_monitor_fragment()

dashboard()

st.caption(
    "GTA 6 Radar V5.7.1 | "
    "Alle transacties zijn simulaties. "
    "De scanner gebruikt DexScreener-marktdata. "
    "De 15-secondenmonitor vereist een actieve sessie. "
    "Supabase bewaart databaseback-ups."
)
