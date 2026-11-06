
"""GTA6 Radar V5.7 — active scanning, 15s monitoring, optional backup."""

import os
import re
import json
import time
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
# CONFIGURATIE
# ============================================================

st.set_page_config(
    page_title="GTA 6 Radar V5.7",
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
    "gta6", "gta 6", "gta vi",
    "grand theft auto", "grandtheftauto", "gtavi"
]

RELATED = [
    "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn", "raul bautista"
]

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6RadarResearch/5.7",
    "Accept": "application/json"
})

LOCK = threading.RLock()
LAST_BACKUP = 0.0


# ============================================================
# HULPFUNCTIES
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
        result = float(value)
        if result != result or abs(result) == float("inf"):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def zero(value):
    return number(value) or 0.0


def matches(value, patterns):
    text = str(value or "").lower()
    return any(
        re.search(
            r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
            text
        )
        for term in patterns
    )


def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def select(sql, args=()):
    with db() as connection:
        return connection.execute(sql, args).fetchall()


# ============================================================
# OPTIONELE PERSISTENTE BACK-UP VIA SUPABASE STORAGE
# ============================================================

def secret(key, default=""):
    if os.getenv(key):
        return os.environ[key]
    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, AttributeError, KeyError):
        return default


def remote_settings():
    url = secret("SUPABASE_URL").rstrip("/")
    key = secret("SUPABASE_SERVICE_ROLE_KEY")
    bucket = secret(
        "SUPABASE_BACKUP_BUCKET",
        "gta6-private-backups"
    )
    object_name = secret(
        "SUPABASE_BACKUP_OBJECT",
        "radar/gta6_radar.db"
    )

    if not url or not key:
        return None

    object_url = (
        url
        + "/storage/v1/object/"
        + quote(bucket, safe="")
        + "/"
        + quote(object_name, safe="/")
    )

    return object_url, key


def restore_backup():
    """
    Restore only when the local SQLite database is absent.
    Never overwrite an existing local database.
    """

    if Path(DB).exists():
        return "Lokale database aanwezig"

    settings = remote_settings()

    if settings is None:
        return "Geen externe back-up ingesteld"

    url, key = settings

    response = requests.get(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}"
        },
        timeout=35
    )

    if response.status_code == 404:
        return "Nog geen externe back-up gevonden"

    response.raise_for_status()

    Path(DB).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".db",
        dir=str(Path(DB).parent)
    ) as tmp:
        tmp.write(response.content)
        temporary_path = tmp.name

    try:
        with sqlite3.connect(
            f"file:{temporary_path}?mode=ro",
            uri=True
        ) as connection:
            result = connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]

            if result != "ok":
                raise sqlite3.DatabaseError(result)

        os.replace(temporary_path, DB)

    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)

    return "Private back-up hersteld"


def backup_remote(force=False):
    """
    Create a consistent SQLite snapshot and upload it to a
    private Supabase Storage bucket.

    This is an optional backup mechanism, not a concurrent
    multi-server database.
    """

    global LAST_BACKUP

    settings = remote_settings()

    if settings is None:
        return "Geen externe opslag ingesteld"

    if (
        not force
        and time.monotonic() - LAST_BACKUP < 120
    ):
        return "Recent geback-upt"

    url, key = settings

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        with sqlite3.connect(DB, timeout=30) as source:
            with sqlite3.connect(tmp.name) as destination:
                source.backup(destination)

        data = Path(tmp.name).read_bytes()

    response = requests.post(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "x-upsert": "true",
            "Content-Type": "application/octet-stream"
        },
        data=data,
        timeout=40
    )

    response.raise_for_status()
    LAST_BACKUP = time.monotonic()

    return f"Back-up opgeslagen ({len(data):,} bytes)"


try:
    BACKUP_BOOT = restore_backup()
except (
    requests.RequestException,
    sqlite3.DatabaseError,
    OSError
) as exc:
    st.error(
        "Externe database kon niet veilig worden hersteld: "
        + str(exc)
    )
    st.stop()


# ============================================================
# DATABASE — BESTAANDE TABELLEN BEHOUDEN
# ============================================================

def initialize():
    statements = [
        """CREATE TABLE IF NOT EXISTS discovered (
            address TEXT PRIMARY KEY,
            name TEXT,
            symbol TEXT,
            first_seen TEXT,
            last_seen TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS tracking_state (
            address TEXT PRIMARY KEY,
            first_seen TEXT,
            last_checked TEXT,
            last_found TEXT,
            missed_scans INTEGER DEFAULT 0,
            status TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS radar_v54 (
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
        )""",

        """CREATE TABLE IF NOT EXISTS paper_v54 (
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
        )""",

        """CREATE TABLE IF NOT EXISTS paper_v542_b (
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
            status TEXT NOT NULL DEFAULT 'OPEN',
            closed_at TEXT,
            exit_market REAL,
            exit_effective REAL,
            pnl_usd REAL,
            exit_reason TEXT,
            last_mark REAL,
            last_mark_at TEXT,
            peak_mark REAL
        )""",

        """CREATE TABLE IF NOT EXISTS v541_runs (
            scanned_at TEXT PRIMARY KEY,
            finished_at TEXT,
            fetched INTEGER,
            errors INTEGER,
            entries INTEGER,
            exits INTEGER,
            cause TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS v541_decisions (
            scan_at TEXT,
            address TEXT,
            name TEXT,
            outcome TEXT,
            reason TEXT,
            price REAL,
            liquidity REAL,
            PRIMARY KEY(scan_at, address)
        )""",

        """CREATE TABLE IF NOT EXISTS v541_config (
            name TEXT PRIMARY KEY,
            value TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS v542_decisions (
            scan_at TEXT NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            strategy TEXT NOT NULL,
            outcome TEXT,
            reason TEXT,
            price REAL,
            liquidity REAL,
            PRIMARY KEY(scan_at, address, strategy)
        )""",

        """CREATE TABLE IF NOT EXISTS v542_runs (
            started_at TEXT PRIMARY KEY,
            finished_at TEXT,
            cause TEXT,
            tokens INTEGER,
            api_errors INTEGER,
            a_entries INTEGER,
            b_entries INTEGER,
            a_exits INTEGER,
            b_exits INTEGER
        )""",

        """CREATE TABLE IF NOT EXISTS v57_monitor (
            checked_at TEXT PRIMARY KEY,
            positions INTEGER,
            pairs INTEGER,
            exits INTEGER,
            api_errors INTEGER,
            backup_status TEXT
        )"""
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
        "v542_decisions": {
            "scan_at": "TEXT",
            "address": "TEXT",
            "name": "TEXT",
            "strategy": "TEXT",
            "outcome": "TEXT",
            "reason": "TEXT",
            "price": "REAL",
            "liquidity": "REAL"
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
        }
    }

    paper_columns = {
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
    }

    migrations["paper_v54"] = paper_columns
    migrations["paper_v542_b"] = paper_columns

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

        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("v542_runs")'
            )
        }

        if "scan_at" in columns:
            connection.execute(
                """UPDATE v542_runs
                   SET started_at=scan_at
                   WHERE started_at IS NULL"""
            )

        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_v54_history
               ON radar_v54(address,scanned_at)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_v54_paper_status
               ON paper_v54(status)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_v542_paper_status
               ON paper_v542_b(status)"""
        )


def get_bool(key, default):
    rows = select(
        "SELECT value FROM v541_config WHERE name=?",
        (key,)
    )
    return rows[0][0] == "1" if rows else default


def set_bool(key, state):
    with db() as connection:
        connection.execute(
            """INSERT INTO v541_config(name,value)
               VALUES(?,?)
               ON CONFLICT(name)
               DO UPDATE SET value=excluded.value""",
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
        if (
            isinstance(address, str)
            and ADDRESS.fullmatch(address)
        ):
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
                title = (
                    f"{base.get('name', '')} "
                    f"{base.get('symbol', '')}"
                )

                if matches(title, STRONG + RELATED):
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

            items = payload if isinstance(payload, list) else []

            for item in items:
                if item.get("chainId") != "solana":
                    continue

                links = " ".join(
                    str(link.get("url", ""))
                    for link in item.get("links") or []
                    if isinstance(link, dict)
                )

                text = (
                    str(item.get("description") or "")
                    + " "
                    + links
                )

                if matches(text, STRONG + RELATED):
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
        "SELECT address FROM watchlist ORDER BY added_at DESC",
        """SELECT address FROM tracking_state
           ORDER BY COALESCE(last_checked,first_seen) ASC
           LIMIT 200""",
        """SELECT address FROM discovered
           ORDER BY last_seen ASC LIMIT 200"""
    ]

    for sql in queries:
        for (address,) in select(sql):
            add(address, "Gevolgd")

    # Inactive tokens are throttled, never deleted.
    protected = {
        row[0]
        for row in select("SELECT address FROM watchlist")
    }

    for table in ("paper_v54", "paper_v542_b"):
        protected |= {
            row[0]
            for row in select(
                f"SELECT address FROM {table} WHERE status='OPEN'"
            )
        }

    recent = select(
        """SELECT r.address,r.scanned_at,r.volume_m5
           FROM radar_v54 r
           INNER JOIN (
               SELECT address,MAX(scanned_at) t
               FROM radar_v54 GROUP BY address
           ) m
           ON m.address=r.address AND m.t=r.scanned_at"""
    )

    inactive = set()

    for address, scanned, volume in recent:
        minutes = (
            datetime.now(timezone.utc) - parse_time(scanned)
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

    return filtered, errors, len(addresses) - len(filtered)


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
                raise ValueError("Onverwacht marktdataformaat")

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

                liq = zero(
                    (pair.get("liquidity") or {}).get("usd")
                )

                old_liq = zero(
                    (current.get("liquidity") or {}).get("usd")
                ) if current else -1

                if liq > old_liq:
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
# HISTORIE EN MOMENTUM
# ============================================================

def history(address, stamp):
    rows = select(
        """SELECT scanned_at,price,volume_m5,pair_address
           FROM radar_v54
           WHERE address=? AND scanned_at<?
           ORDER BY scanned_at DESC LIMIT 16""",
        (address, stamp)
    )

    return [
        (t, number(price), number(volume), pair)
        for t, price, volume, pair in rows
    ]


def signals(address, stamp, price, volume, pair):
    if price is None or price <= 0:
        return (
            0, False,
            "Prijs ontbreekt of is ongeldig",
            "ONVOLLEDIGE_DATA"
        )

    if volume is None:
        return (
            0, False,
            "5m-volume ontbreekt",
            "ONVOLLEDIGE_DATA"
        )

    if volume <= 0:
        return (
            0, False,
            "5m-volume is $0; geen actieve handel",
            "GEEN_HANDEL"
        )

    previous = [
        row for row in history(address, stamp)
        if row[1] is not None
        and row[1] > 0
        and row[2] is not None
        and row[2] > 0
        and row[3] == pair
    ]

    if len(previous) < 2:
        return (
            0, False,
            f"{len(previous)+1}/3 metingen voor hetzelfde pair",
            "TE_WEINIG_METINGEN"
        )

    recent = next(
        (
            row for row in previous
            if 3.5 <= (
                parse_time(stamp) - parse_time(row[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )

    old = next(
        (
            row for row in previous
            if recent
            and 3.5 <= (
                parse_time(recent[0]) - parse_time(row[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )

    if recent is None or old is None:
        return (
            0, False,
            "Geen drie metingen met telkens 3,5–20 minuten ertussen",
            "ONGELDIG_INTERVAL"
        )

    gain = price / old[1] - 1
    p3 = old[1] < recent[1] < price
    v3 = old[2] < recent[2] < volume

    a_signal = (
        p3
        and gain >= 0.02
        and v3
        and volume >= 250
    )

    b_signal = (
        gain >= 0.05
        and price > recent[1]
        and volume >= 250
    )

    a_score = 80 if a_signal else 30 if (p3 or v3) else 0

    explanation = (
        f"Pair {pair[:9]}…; "
        f"koers {gain:+.1%}; "
        f"prijs stijgt 3x={p3}; "
        f"volume stijgt 3x={v3}; "
        f"volume=${volume:,.0f}; "
        f"A voldaan={a_signal}; "
        f"B voldaan={b_signal}"
    )

    return a_score, b_signal, explanation, "GEMETEN"


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
            0.0,
            (
                parse_time(stamp).timestamp() * 1000 - created
            ) / 3600000
        )
        if created else None
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

    a_score, b_signal, evidence, diagnostic = signals(
        address, stamp, price, volume, pair_id
    )

    tx_count = buys + sells

    average_trade = (
        volume / tx_count
        if tx_count and volume is not None
        else None
    )

    micro = (
        tx_count >= 100
        and average_trade is not None
        and average_trade < 3
    )

    warnings = []

    if liquidity is None or liquidity < 10000:
        warnings.append("Liquiditeit onbekend of < $10.000")

    if micro:
        warnings.append("Microtransacties; mogelijke bots")

    warnings.append(
        "Geen controle op mint/freeze, houders of verkoopbaarheid"
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
        "a_score": a_score,
        "b_signal": b_signal,
        "evidence": evidence,
        "diagnostic": diagnostic,
        "warnings": "; ".join(warnings)
    }


# ============================================================
# METINGEN OPSLAAN
# ============================================================

def record_observations(records, stamp):
    with db() as connection:
        for r in records:
            connection.execute(
                """INSERT INTO discovered
                   (address,name,symbol,first_seen,last_seen)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(address) DO UPDATE SET
                   name=excluded.name,
                   symbol=excluded.symbol,
                   last_seen=excluded.last_seen""",
                (
                    r["address"],
                    r["name"],
                    r["symbol"],
                    stamp,
                    stamp
                )
            )

            connection.execute(
                """INSERT INTO tracking_state
                   (address,first_seen,last_checked,last_found,
                    missed_scans,status)
                   VALUES(?,?,?,?,0,'ACTIEF')
                   ON CONFLICT(address) DO UPDATE SET
                   last_checked=excluded.last_checked,
                   last_found=excluded.last_found,
                   missed_scans=0,
                   status='ACTIEF'""",
                (r["address"], stamp, stamp, stamp)
            )

            phase = (
                "MOMENTUM TER CONTROLE"
                if r["a_score"] == 80
                else "PRE-TREND KANDIDAAT"
                if r["age"] is not None and r["age"] < 24
                else "MONITOREN"
            )

            connection.execute(
                """INSERT OR REPLACE INTO radar_v54
                   (address,scanned_at,name,symbol,pair_address,
                    source,price,liquidity,volume_m5,buys_m5,
                    sells_m5,age_hours,narrative,momentum,
                    evidence,phase,warnings)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["address"],
                    stamp,
                    r["name"],
                    r["symbol"],
                    r["pair"],
                    r["source"],
                    r["price"],
                    r["liquidity"],
                    r["volume"],
                    r["buys"],
                    r["sells"],
                    r["age"],
                    r["narrative"],
                    r["a_score"],
                    r["evidence"],
                    phase,
                    r["warnings"]
                )
            )


# ============================================================
# STRATEGIE A EN B
# ============================================================

def decide(r, strategy, cfg, used):
    if r["address"] in used:
        return (
            "GEEN_INSTAP",
            "Deze token werd in deze strategie al eens virtueel verhandeld"
        )

    if r["narrative"] < 65:
        return "GEEN_INSTAP", "Geen duidelijke GTA-narrative"

    if r["diagnostic"] != "GEMETEN":
        return (
            "GEEN_INSTAP",
            r["diagnostic"] + ": " + r["evidence"]
        )

    if strategy == "A" and r["a_score"] != 80:
        return (
            "GEEN_INSTAP",
            "Strategie A voldoet niet: " + r["evidence"]
        )

    if strategy == "B" and not r["b_signal"]:
        return (
            "GEEN_INSTAP",
            "Strategie B voldoet niet: " + r["evidence"]
        )

    if r["price"] is None or r["price"] <= 0:
        return "GEBLOKKEERD", "Prijs ongeldig"

    if (
        r["liquidity"] is None
        or r["liquidity"] < cfg["min_liq"]
    ):
        return "GEBLOKKEERD", "Onvoldoende liquiditeit"

    if r["age"] is None or r["age"] > 24:
        return (
            "GEBLOKKEERD",
            "Handelspaar ouder dan 24 uur of leeftijd onbekend"
        )

    if r["volume"] is None or r["volume"] < 250:
        return "GEBLOKKEERD", "Onvoldoende volume"

    if r["buys"] <= r["sells"]:
        return (
            "GEBLOKKEERD",
            "Aantal aankopen niet groter dan verkopen"
        )

    if strategy == "A" and r["micro"]:
        return (
            "GEBLOKKEERD",
            "Veel microtransacties; mogelijke bots"
        )

    return (
        "KANDIDAAT",
        "Kwantitatieve voorwaarden gehaald; "
        "contractveiligheid niet gecontroleerd"
    )


def paper_run(records, stamp, cfg, active_a, active_b):
    by_address = {
        r["address"]: r
        for r in records
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
        enabled = active_a if strategy == "A" else active_b

        with db() as connection:
            open_trades = connection.execute(
                f"""SELECT id,address,opened_at,entry_market,
                    quantity,amount_usd,fee_rate,slippage_rate,
                    pair_address
                    FROM {table}
                    WHERE status='OPEN'"""
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

                r = by_address.get(address)

                if not r or not r["price"] or r["price"] <= 0:
                    continue

                if original_pair and r["pair"] != original_pair:
                    connection.execute(
                        f"""UPDATE {table}
                            SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                            WHERE id=?""",
                        (trade_id,)
                    )
                    continue

                price = r["price"]

                connection.execute(
                    f"""UPDATE {table}
                        SET last_mark=?,last_mark_at=?,
                        peak_mark=MAX(COALESCE(peak_mark,0),?)
                        WHERE id=?""",
                    (price, stamp, price, trade_id)
                )

                change = price / entry - 1

                hours = (
                    parse_time(stamp) - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= cfg["take"]
                    else "STOP_LOSS"
                    if change <= -cfg["stop"]
                    else "TIME_EXIT"
                    if hours >= cfg["max_hours"]
                    else None
                )

                if reason is None:
                    continue

                if (
                    r["liquidity"] is None
                    or r["liquidity"] < cfg["min_liq"]
                ):
                    connection.execute(
                        f"""UPDATE {table}
                            SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                            WHERE id=?""",
                        (trade_id,)
                    )
                    continue

                effective = price * (1 - slip)
                pnl = quantity * effective * (1 - fee) - amount

                cursor = connection.execute(
                    f"""UPDATE {table}
                        SET status='CLOSED',
                        closed_at=?,exit_market=?,
                        exit_effective=?,pnl_usd=?,
                        exit_reason=?
                        WHERE id=? AND status='OPEN'""",
                    (
                        stamp, price, effective,
                        pnl, reason, trade_id
                    )
                )

                results[strategy][1] += cursor.rowcount

            open_count = connection.execute(
                f"""SELECT COUNT(*) FROM {table}
                    WHERE status='OPEN'"""
            ).fetchone()[0]

            for r in records:
                outcome, reason = decide(
                    r, strategy, cfg, used
                )

                if outcome == "KANDIDAAT" and not enabled:
                    outcome = "UITGESCHAKELD"
                    reason = f"Strategie {strategy} staat uit"

                elif (
                    outcome == "KANDIDAAT"
                    and open_count >= cfg["max_positions"]
                ):
                    outcome = "GEBLOKKEERD"
                    reason = "Maximum aantal posities bereikt"

                elif outcome == "KANDIDAAT":
                    effective_entry = (
                        r["price"] * (1 + cfg["slip"])
                    )

                    quantity = (
                        cfg["amount"] * (1 - cfg["fee"])
                        / effective_entry
                    )

                    connection.execute(
                        f"""INSERT INTO {table}
                            (address,name,pair_address,opened_at,
                             entry_market,entry_effective,
                             amount_usd,quantity,fee_rate,
                             slippage_rate,status,last_mark,
                             last_mark_at,peak_mark)
                            VALUES(?,?,?,?,?,?,?,?,?,?,
                                   'OPEN',?,?,?)""",
                        (
                            r["address"],
                            r["name"],
                            r["pair"],
                            stamp,
                            r["price"],
                            effective_entry,
                            cfg["amount"],
                            quantity,
                            cfg["fee"],
                            cfg["slip"],
                            r["price"],
                            stamp,
                            r["price"]
                        )
                    )

                    used.add(r["address"])
                    open_count += 1
                    results[strategy][0] += 1

                    outcome = "PAPER_ENTRY"
                    reason = (
                        "Virtuele instap geregistreerd; "
                        "uitvoerbaarheid niet gegarandeerd"
                    )

                connection.execute(
                    """INSERT OR REPLACE INTO v542_decisions
                       (scan_at,address,name,strategy,outcome,
                        reason,price,liquidity)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        stamp,
                        r["address"],
                        r["name"],
                        strategy,
                        outcome,
                        reason,
                        r["price"],
                        r["liquidity"]
                    )
                )

    return results


# ============================================================
# VOLLEDIGE MARKTSCAN — IEDERE 5 MINUTEN
# ============================================================

def scan(trigger, cfg, active_a, active_b):
    with LOCK:
        return scan_locked(
            trigger, cfg, active_a, active_b
        )


def scan_locked(trigger, cfg, active_a, active_b):
    start = utcnow()

    addresses, errors, cooled = discover()

    pairs, fetched, fetch_errors = market_pairs(
        list(addresses)
    )

    errors.extend(fetch_errors)

    observed = []

    open_addresses = {
        row[0]
        for row in select(
            "SELECT address FROM paper_v54 WHERE status='OPEN'"
        )
    }

    open_addresses |= {
        row[0]
        for row in select(
            "SELECT address FROM paper_v542_b WHERE status='OPEN'"
        )
    }

    for address, pair in pairs.items():
        try:
            r = analyze(
                address,
                pair,
                addresses[address],
                start
            )

            if (
                r["narrative"] >= 65
                or address in open_addresses
            ):
                observed.append(r)

        except (ValueError, KeyError, TypeError) as exc:
            errors.append(
                f"Analyse {address[:10]}: {exc}"
            )

    record_observations(observed, start)

    results = paper_run(
        observed,
        start,
        cfg,
        active_a,
        active_b
    )

    end = utcnow()

    with db() as connection:
        connection.execute(
            """INSERT INTO v542_runs
               (started_at,finished_at,cause,tokens,
                api_errors,a_entries,b_entries,a_exits,b_exits)
               VALUES(?,?,?,?,?,?,?,?,?)""",
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
        sqlite3.Error
    ) as exc:
        errors.append(
            "Externe back-up mislukt: " + str(exc)
        )

    return len(observed), results, errors, cooled


# ============================================================
# APARTE PAPERTRADE-MONITOR — IEDERE 15 SECONDEN
# ============================================================

def monitor_positions(cfg):
    if not LOCK.acquire(blocking=False):
        return None

    try:
        ledgers = [
            ("A", "paper_v54"),
            ("B", "paper_v542_b")
        ]

        open_positions = []

        for strategy, table in ledgers:
            trades = select(
                f"""SELECT id,address,pair_address,opened_at,
                    entry_market,quantity,amount_usd,
                    fee_rate,slippage_rate
                    FROM {table}
                    WHERE status='OPEN'"""
            )

            for trade in trades:
                open_positions.append(
                    (strategy, table, trade)
                )

        if not open_positions:
            return {
                "open": 0,
                "checked": 0,
                "exits": 0,
                "errors": [],
                "backup": "-"
            }

        addresses = sorted({
            item[2][1]
            for item in open_positions
        })

        pairs, fetched, errors = market_pairs(addresses)

        stamp = utcnow()
        exits = 0

        with db() as connection:
            for strategy, table, trade in open_positions:
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

                if original_pair and pair_id != original_pair:
                    connection.execute(
                        f"""UPDATE {table}
                            SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                            WHERE id=? AND status='OPEN'""",
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
                    f"""UPDATE {table}
                        SET last_mark=?,last_mark_at=?,
                        peak_mark=MAX(COALESCE(peak_mark,0),?)
                        WHERE id=? AND status='OPEN'""",
                    (
                        price,
                        stamp,
                        price,
                        trade_id
                    )
                )

                change = price / entry - 1

                hours = (
                    parse_time(stamp)
                    - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= cfg["take"]
                    else "STOP_LOSS"
                    if change <= -cfg["stop"]
                    else "TIME_EXIT"
                    if hours >= cfg["max_hours"]
                    else None
                )

                if reason is None:
                    continue

                if (
                    liquidity is None
                    or liquidity < cfg["min_liq"]
                ):
                    connection.execute(
                        f"""UPDATE {table}
                            SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                            WHERE id=? AND status='OPEN'""",
                        (trade_id,)
                    )
                    continue

                effective_exit = (
                    price * (1 - zero(slip))
                )

                pnl = (
                    quantity
                    * effective_exit
                    * (1 - zero(fee))
                    - amount
                )

                cursor = connection.execute(
                    f"""UPDATE {table}
                        SET status='CLOSED',
                        closed_at=?,
                        exit_market=?,
                        exit_effective=?,
                        pnl_usd=?,
                        exit_reason=?
                        WHERE id=? AND status='OPEN'""",
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
                """INSERT OR REPLACE INTO v57_monitor
                   (checked_at,positions,pairs,exits,
                    api_errors,backup_status)
                   VALUES(?,?,?,?,?,?)""",
                (
                    stamp,
                    len(open_positions),
                    len(pairs),
                    exits,
                    len(errors),
                    "PENDING"
                )
            )

        try:
            backup_status = backup_remote(
                force=exits > 0
            )
        except (
            requests.RequestException,
            OSError,
            sqlite3.Error
        ) as exc:
            backup_status = (
                "BACKUP ERROR: " + str(exc)
            )
            errors.append(backup_status)

        with db() as connection:
            connection.execute(
                """UPDATE v57_monitor
                   SET backup_status=?
                   WHERE checked_at=?""",
                (backup_status, stamp)
            )

        return {
            "open": len(open_positions),
            "checked": len(pairs),
            "exits": exits,
            "errors": errors,
            "backup": backup_status
        }

    finally:
        LOCK.release()


# ============================================================
# DASHBOARD-DATA
# ============================================================

def latest_tokens():
    rows = select(
        """SELECT started_at FROM v542_runs
           ORDER BY started_at DESC LIMIT 1"""
    )

    if not rows:
        return pd.DataFrame()

    data = select(
        """SELECT name,symbol,address,pair_address,
           age_hours,price,liquidity,volume_m5,
           buys_m5,sells_m5,momentum,evidence,warnings
           FROM radar_v54
           WHERE scanned_at=?""",
        (rows[0][0],)
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
        f"""SELECT id,name,address,opened_at,
            amount_usd,entry_market,last_mark,
            status,pnl_usd,exit_reason
            FROM {table}
            ORDER BY id DESC LIMIT 300"""
    )

    data = pd.DataFrame(
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

    if not data.empty:
        data["Geopend België"] = data[
            "Open UTC"
        ].apply(be_time)

        data = data.drop(columns=["Open UTC"])

    return data


def paper_risk_frame(table):
    rows = select(
        f"""SELECT id,name,address,status,amount_usd,
            quantity,fee_rate,slippage_rate,
            entry_market,opened_at,pair_address,
            last_mark,last_mark_at,exit_reason
            FROM {table}
            WHERE status='OPEN'
            ORDER BY id DESC"""
    )

    result = []

    for trade in rows:
        (
            trade_id,
            name,
            address,
            status,
            amount,
            quantity,
            fee,
            slip,
            entry,
            opened,
            original_pair,
            mark,
            mark_at,
            reason
        ) = trade

        latest = select(
            """SELECT scanned_at,price,liquidity,pair_address
               FROM radar_v54
               WHERE address=?
               ORDER BY scanned_at DESC LIMIT 1""",
            (address,)
        )

        current = latest[0] if latest else None

        pair_changed = bool(
            current
            and original_pair
            and current[3] != original_pair
        )

        # Prefer last price checked by the 15-second monitor.
        if mark_at and mark and not pair_changed:
            price = mark
            age = (
                datetime.now(timezone.utc)
                - parse_time(mark_at)
            ).total_seconds() / 60
        else:
            price = (
                current[1]
                if current and not pair_changed
                else None
            )
            age = (
                (
                    datetime.now(timezone.utc)
                    - parse_time(current[0])
                ).total_seconds() / 60
                if current else None
            )

        liquidity = current[2] if current else None

        sale = (
            quantity * price
            * (1 - zero(slip))
            * (1 - zero(fee))
            if price and quantity
            else None
        )

        pnl = (
            sale - amount
            if sale is not None and amount is not None
            else None
        )

        net_pct = (
            100 * pnl / amount
            if pnl is not None and amount
            else None
        )

        warning = (
            "PAIR WISSEL"
            if pair_changed
            else "GEEN ACTUELE PRIJS"
            if price is None
            else "DATA OUDER DAN 20 MIN"
            if age is not None and age > 20
            else "LIQUIDITEIT ONBEKEND / TE LAAG"
            if liquidity is None or liquidity < 10000
            else "NETTO <= -15%"
            if net_pct is not None and net_pct <= -15
            else "NETTO <= -10%"
            if net_pct is not None and net_pct <= -10
            else "Verkoopbaarheid niet geverifieerd"
        )

        result.append({
            "ID": trade_id,
            "Naam": name,
            "Adres": address,
            "Marktprijs": price,
            "Liquiditeit": liquidity,
            "Netto mark P/L %": net_pct,
            "Netto mark P/L $": pnl,
            "Data leeftijd (min)": age,
            "Waarschuwing": warning,
            "Exit_status": reason or "OPEN"
        })

    return pd.DataFrame(result)


# ============================================================
# LEARNING DATASET — HANDMATIGE GITHUB-IMPORT
# ============================================================

def learning_panel():
    st.subheader(
        "🧠 Learning Engine — nog geen getraind AI-model"
    )

    st.caption(
        "De GitHub Learning Collector heeft een aparte database. "
        "Upload de bestanden om de resultaten hier te analyseren."
    )

    uploaded_json = st.file_uploader(
        "Optioneel: data/latest_scan.json",
        type=["json"],
        key="learning_json"
    )

    if uploaded_json is not None:
        try:
            payload = json.load(uploaded_json)

            if not isinstance(payload, dict):
                raise ValueError("JSON-object verwacht")

            keys = [
                "discovered",
                "recorded",
                "total_snapshots",
                "total_labels"
            ]

            labels = [
                "Ontdekt",
                "Deze run",
                "Totaal snapshots",
                "Trainingslabels"
            ]

            columns = st.columns(4)

            for column, key, label in zip(
                columns, keys, labels
            ):
                column.metric(
                    label,
                    str(payload.get(key, "-"))
                )

            st.write(
                "Laatste collector-run (UTC):",
                payload.get("finished_at_utc", "-")
            )

            errors = payload.get("errors", [])

            if errors:
                st.warning(
                    f"Collector meldde {len(errors)} fouten"
                )
                st.write(errors[:10])

        except (
            ValueError,
            json.JSONDecodeError,
            UnicodeError
        ) as exc:
            st.error(f"Ongeldig statusbestand: {exc}")

    uploaded_db = st.file_uploader(
        "Optioneel: data/learning.db",
        type=["db"],
        key="learning_db"
    )

    if uploaded_db is None:
        st.info(
            "Geen Learning-database geüpload. "
            "Er is nog geen automatische GitHub-synchronisatie."
        )
        return

    try:
        if uploaded_db.size > 100_000_000:
            st.error("Database te groot (maximaal 100 MB).")
            return

        with tempfile.NamedTemporaryFile(
            suffix=".db"
        ) as tmp:
            tmp.write(uploaded_db.getvalue())
            tmp.flush()

            uri = (
                "file:" + tmp.name
                + "?mode=ro&immutable=1"
            )

            with sqlite3.connect(
                uri,
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
                    "Uitkomsten 15/30/60m",
                    outcomes
                )

                if outcomes:
                    df = pd.read_sql_query(
                        """SELECT horizon_minutes,
                           COUNT(*) AS n,
                           AVG(return_pct) AS avg_pct,
                           AVG(crash_30pct) AS crash_share
                           FROM outcomes
                           GROUP BY horizon_minutes
                           ORDER BY horizon_minutes""",
                        connection
                    )

                    df["crash_share"] *= 100
                    st.dataframe(
                        df,
                        hide_index=True,
                        use_container_width=True
                    )

                latest = pd.read_sql_query(
                    """SELECT name,token_address,
                       pair_address,scanned_at,price_usd,
                       liquidity_usd,volume_m5
                       FROM snapshots
                       ORDER BY scanned_at DESC
                       LIMIT 150""",
                    connection
                )

                st.dataframe(
                    latest,
                    hide_index=True,
                    use_container_width=True
                )

    except (sqlite3.Error, ValueError) as exc:
        st.error(
            "Learning-DB kon niet worden gelezen: "
            + str(exc)
        )


# ============================================================
# STREAMLIT INTERFACE
# ============================================================

initialize()

st.title("🎮 GTA 6 Radar V5.7")

st.caption(
    "A/B-paperstrategieën · 15 seconden papercontrole · "
    "actieve tokens eerst · bestaande database behouden"
)

st.warning(
    "Dit systeem voert uitsluitend virtuele transacties uit. "
    "Een gesimuleerde stoploss is geen gegarandeerde verkoop. "
    "Contractveiligheid, walletconcentratie en honeypots "
    "worden niet volledig gecontroleerd."
)

if "v542_auto" not in st.session_state:
    st.session_state.v542_auto = get_bool(
        "auto_scan", True
    )

if "v542_a" not in st.session_state:
    st.session_state.v542_a = get_bool(
        "paper", False
    )

if "v542_b" not in st.session_state:
    st.session_state.v542_b = get_bool(
        "paper_b", False
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


if remote_settings():
    st.sidebar.success(
        "Private Supabase-back-up ingesteld"
    )
else:
    st.sidebar.warning(
        "Geen externe back-up! "
        "SQLite op Streamlit Cloud is niet permanent."
    )

st.sidebar.caption(BACKUP_BOOT)

st.sidebar.header("⚙️ Scanner")

st.sidebar.toggle(
    "Automatisch scannen (actieve sessie)",
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
    "Marktscaninterval (minuten)",
    [5, 10, 15],
    index=0
)

st.sidebar.caption(
    "Papertradecontrole: elke 15 seconden. "
    "Marktscan standaard elke 5 minuten."
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
    "Max. open posities per strategie",
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
    "Max. looptijd (uur)",
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
# PAPER MONITOR — 15 SECONDEN
# ============================================================

@st.fragment(run_every="15s")
def paper_monitor_fragment():
    st.subheader(
        "⚡ Papertrade-monitor — 15 seconden"
    )

    st.caption(
        "De monitor controleert open posities zolang "
        "de Streamlit-sessie actief blijft."
    )

    result = monitor_positions(settings)

    if result is None:
        st.info(
            "Hoofdscan actief; papercontrole wordt daarna hervat."
        )

    elif result["open"] == 0:
        st.info(
            "Geen open papertrades. "
            "Er zijn geen extra koersverzoeken nodig."
        )

    else:
        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Open posities",
            result["open"]
        )

        c2.metric(
            "Tokens met prijsdata",
            result["checked"]
        )

        c3.metric(
            "Virtuele exits",
            result["exits"]
        )

        if result["errors"]:
            st.warning(
                "Monitor/backup: "
                + " | ".join(result["errors"][:3])
            )

    rows = select(
        """SELECT checked_at,positions,pairs,
           exits,backup_status
           FROM v57_monitor
           ORDER BY checked_at DESC
           LIMIT 5"""
    )

    if rows:
        st.dataframe(
            pd.DataFrame(
                rows,
                columns=[
                    "UTC",
                    "Posities",
                    "Pairs",
                    "Exits",
                    "Back-up"
                ]
            ),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# HOOFDDASHBOARD — 60 SECONDEN CONTROLE
# ============================================================

@st.fragment(run_every="60s")
def dashboard():
    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    last = select(
        """SELECT started_at,finished_at,cause
           FROM v542_runs
           ORDER BY started_at DESC
           LIMIT 1"""
    )

    last_end = (
        parse_time(last[0][1])
        if last and last[0][1]
        else None
    )

    current = datetime.now(timezone.utc)

    next_scan = (
        last_end + timedelta(minutes=interval)
        if last_end
        else current
    )

    st.subheader("🕒 Scanplanning — Belgische tijd")

    st.write(
        "Automatisch scannen:",
        "**AAN**"
        if st.session_state.v542_auto
        else "**UIT**"
    )

    st.write(
        "Laatste scan:",
        f"**{be_time(last[0][1])}** ({last[0][2]})"
        if last
        else "Nog geen scan"
    )

    st.write(
        "Volgende scan vanaf:",
        f"**{be_time(next_scan.isoformat())}**"
        if st.session_state.v542_auto
        else "Niet ingepland"
    )

    st.caption(
        "De pagina controleert ongeveer iedere minuut "
        "of een nieuwe marktscan nodig is. "
        "Dit is geen gegarandeerde 24/7-scheduler."
    )

    due = (
        st.session_state.v542_auto
        and current >= next_scan
    )

    if manual or due:
        try:
            with st.spinner(
                "Solana-tokens scannen en strategieën evalueren..."
            ):
                count, results, errors, cooled = scan(
                    "MANUEEL" if manual else "AUTOMATISCH",
                    settings,
                    st.session_state.v542_a,
                    st.session_state.v542_b
                )

            st.success(
                f"Scan klaar: {count} tokens · "
                f"{cooled} inactieve tokens tijdelijk overgeslagen · "
                f"A: {results['A'][0]} instappen · "
                f"B: {results['B'][0]} instappen"
            )

            if errors:
                with st.expander(
                    f"{len(errors)} API-/datameldingen"
                ):
                    st.write(errors[:35])

        except (
            sqlite3.Error,
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            st.error(f"Scanfout: {exc}")

    tokens = latest_tokens()

    a_open = select(
        """SELECT COUNT(*) FROM paper_v54
           WHERE status='OPEN'"""
    )[0][0]

    b_open = select(
        """SELECT COUNT(*) FROM paper_v542_b
           WHERE status='OPEN'"""
    )[0][0]

    a, b, c, d = st.columns(4)

    a.metric(
        "Tokens laatste scan",
        len(tokens)
    )

    b.metric(
        "A: momentum 80",
        int((tokens["A momentum"] == 80).sum())
        if not tokens.empty else 0
    )

    c.metric(
        "A: open posities",
        a_open
    )

    d.metric(
        "B: open posities",
        b_open
    )

    # --------------------------------------------------------
    # ACTIEVE TOKENS
    # --------------------------------------------------------

    st.subheader("📡 Actieve tokens")

    if tokens.empty:
        st.info(
            "Nog geen tokens geregistreerd. "
            "Klik op Scan nu."
        )

    else:
        active_tokens = tokens[
            tokens["5m-volume $"].fillna(0) >= 25
        ].copy()

        quiet_tokens = tokens[
            tokens["5m-volume $"].fillna(0) < 25
        ].copy()

        st.caption(
            f"{len(active_tokens)} tokens met minstens "
            f"$25 handelsvolume in 5 minuten."
        )

        if not active_tokens.empty:
            active_tokens = active_tokens.sort_values(
                ["5m-volume $", "A momentum"],
                ascending=False,
                na_position="last"
            )

            st.dataframe(
                active_tokens,
                hide_index=True,
                use_container_width=True
            )

        else:
            st.info(
                "Geen tokens boven de actieve volumedrempel."
            )

        with st.expander(
            f"💤 Minder actieve tokens ({len(quiet_tokens)})"
        ):
            st.dataframe(
                quiet_tokens,
                hide_index=True,
                use_container_width=True
            )

        st.download_button(
            "📥 Alle tokenmetingen CSV",
            tokens.to_csv(index=False).encode("utf-8"),
            "gta6_v57_tokens.csv",
            "text/csv"
        )

        choices = {
            f"{r['Naam']} · {r['Adres'][:12]}": r["Adres"]
            for _, r in tokens.iterrows()
        }

        chosen = st.selectbox(
            "Tokenhistoriek",
            list(choices)
        )

        address = choices[chosen]

        one = tokens[
            tokens["Adres"] == address
        ].iloc[0]

        st.code(address, language="text")

        st.link_button(
            "Bekijk DexScreener",
            "https://dexscreener.com/solana/"
            + str(one["Pair"] or address)
        )

        if st.button("⭐ Voeg toe aan watchlist"):
            with db() as connection:
                connection.execute(
                    """INSERT OR IGNORE INTO watchlist
                       (address,name,added_at)
                       VALUES(?,?,?)""",
                    (
                        address,
                        one["Naam"],
                        utcnow()
                    )
                )

            st.success("Toegevoegd aan watchlist")

        hist = select(
            """SELECT scanned_at,price,volume_m5
               FROM radar_v54
               WHERE address=?
               ORDER BY scanned_at DESC
               LIMIT 120""",
            (address,)
        )

        if len(hist) >= 2:
            hd = pd.DataFrame(
                hist,
                columns=[
                    "Tijd",
                    "Prijs $",
                    "5m-volume $"
                ]
            )

            hd["Tijd"] = pd.to_datetime(
                hd["Tijd"],
                utc=True
            ).dt.tz_convert("Europe/Brussels")

            hd = hd.sort_values("Tijd").set_index("Tijd")

            st.line_chart(hd[["Prijs $"]])
            st.line_chart(hd[["5m-volume $"]])

    # --------------------------------------------------------
    # BESLISSINGEN A/B
    # --------------------------------------------------------

    st.subheader("🔎 Vergelijking beslissingen A en B")

    newest = select(
        """SELECT started_at FROM v542_runs
           ORDER BY started_at DESC LIMIT 1"""
    )

    if newest:
        rows = select(
            """SELECT name,address,strategy,outcome,
               reason,price,liquidity
               FROM v542_decisions
               WHERE scan_at=?
               ORDER BY strategy,outcome,name""",
            (newest[0][0],)
        )

        decisions = pd.DataFrame(
            rows,
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

        filter_strategy = st.radio(
            "Toon strategie",
            ["Allebei", "A", "B"],
            horizontal=True
        )

        view = (
            decisions
            if filter_strategy == "Allebei"
            else decisions[
                decisions["Strategie"] == filter_strategy
            ]
        )

        st.dataframe(
            view,
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            "📥 A/B-beslissingen CSV",
            decisions.to_csv(index=False).encode("utf-8"),
            "gta6_v57_ab_beslissingen.csv",
            "text/csv"
        )

    # --------------------------------------------------------
    # PAPERPORTFOLIO
    # --------------------------------------------------------

    st.subheader("🧪 Paperportfolio's")

    tab_a, tab_b = st.tabs([
        "Strategie A — streng",
        "Strategie B — experimenteel"
    ])

    for tab, table in [
        (tab_a, "paper_v54"),
        (tab_b, "paper_v542_b")
    ]:
        with tab:
            trades = ledger_view(table)

            if trades.empty:
                st.info(
                    "Nog geen virtuele posities in deze strategie."
                )

            else:
                closed = trades[
                    trades["Status"] == "CLOSED"
                ]

                cols = st.columns(3)

                cols[0].metric(
                    "Totaal geregistreerde posities",
                    len(trades)
                )

                cols[1].metric(
                    "Gerealiseerd P/L",
                    f"${closed['Gerealiseerd P/L $'].sum():,.2f}"
                )

                winrate = (
                    (closed["Gerealiseerd P/L $"] > 0).mean()
                    * 100
                    if not closed.empty
                    else None
                )

                cols[2].metric(
                    "Winrate gesloten",
                    f"{winrate:.1f}%"
                    if winrate is not None
                    else "-"
                )

                st.dataframe(
                    trades,
                    hide_index=True,
                    use_container_width=True
                )

                st.download_button(
                    f"📥 Trades {table} CSV",
                    trades.to_csv(index=False).encode("utf-8"),
                    f"{table}.csv",
                    "text/csv"
                )

    # --------------------------------------------------------
    # RISICO
    # --------------------------------------------------------

    st.subheader("🚨 Risicobewaking — open paperposities")

    st.caption(
        "Getoonde resultaten zijn schattingen. "
        "Liquiditeit en daadwerkelijke verkoopbaarheid "
        "zijn niet gegarandeerd."
    )

    for name, table in [
        ("Strategie A", "paper_v54"),
        ("Strategie B", "paper_v542_b")
    ]:
        risk = paper_risk_frame(table)

        if not risk.empty:
            st.write("**" + name + "**")

            st.dataframe(
                risk,
                hide_index=True,
                use_container_width=True
            )

    # --------------------------------------------------------
    # LEARNING DATA
    # --------------------------------------------------------

    with st.expander(
        "🧠 Learning-dataset onderzoeken (GitHub-export)"
    ):
        learning_panel()

    # --------------------------------------------------------
    # SCANLOG
    # --------------------------------------------------------

    st.subheader("🕒 Scanlog — Belgische tijd")

    logs = select(
        """SELECT started_at,finished_at,cause,
           tokens,api_errors,a_entries,b_entries,
           a_exits,b_exits
           FROM v542_runs
           ORDER BY started_at DESC
           LIMIT 40"""
    )

    if logs:
        df = pd.DataFrame(
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

        df["Begin België"] = df["Begin UTC"].apply(be_time)
        df["Einde België"] = df["Einde UTC"].apply(be_time)

        st.dataframe(
            df.drop(columns=["Begin UTC", "Einde UTC"]),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# START DASHBOARDS
# ============================================================

paper_monitor_fragment()
dashboard()

st.caption(
    "GTA 6 Radar V5.7 — uitsluitend papertrading. "
    "De 15-secondenmonitor en de vijfminutenscanner draaien "
    "alleen tijdens een actieve Streamlit-sessie. "
    "Externe opslag vereist afzonderlijke configuratie."
)
