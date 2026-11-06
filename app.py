
"""GTA 6 Token Radar V5.7 — papertrading, 15s monitor, Supabase backup."""

import os
import re
import json
import time
import sqlite3
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GTA 6 Radar V5.7", page_icon="🎮", layout="wide")

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
API = "https://api.dexscreener.com"
LOCAL = ZoneInfo("Europe/Brussels")
LOCK = threading.RLock()
LAST_BACKUP = 0.0

SEARCHES = [
    "GTA6", "GTA VI", "Grand Theft Auto", "Vice City",
    "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn"
]
STRONG = ["gta6", "gta 6", "gta vi", "grand theft auto", "grandtheftauto", "gtavi"]
RELATED = ["vice city", "leonida", "lucia camino", "jason duval",
           "port gellhorn", "raul bautista"]
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6RadarResearch/5.7",
    "Accept": "application/json"
})


# ============================================================
# BASISFUNCTIES
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def belgian_time(value):
    return parse(value).astimezone(LOCAL).strftime("%d-%m-%Y %H:%M:%S") if value else "-"


def num(value):
    try:
        v = float(value)
        return v if v == v and abs(v) != float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def zero(value):
    return num(value) or 0.0


def match(text, words):
    value = str(text or "").lower()
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", value)
        for w in words
    )


def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def query(sql, parameters=()):
    with connect() as c:
        return c.execute(sql, parameters).fetchall()


def get_secret(name, default=""):
    if os.getenv(name):
        return os.environ[name]
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, AttributeError, KeyError):
        return default


# ============================================================
# SUPABASE — NIEUWE sb_secret_ API SLEUTEL
# ============================================================

def storage_config():
    project = get_secret("SUPABASE_URL").rstrip("/")
    key = get_secret("SUPABASE_SECRET_KEY")
    bucket = get_secret("SUPABASE_BACKUP_BUCKET", "gta6-private-backups")
    object_name = get_secret("SUPABASE_BACKUP_OBJECT", "radar/gta6_radar.db")

    if not project or not key:
        return None

    if not project.startswith("https://") or not key.startswith("sb_secret_"):
        raise ValueError("Ongeldige Supabase-URL of Secret Key")

    address = (
        project + "/storage/v1/object/"
        + quote(bucket, safe="") + "/"
        + quote(object_name, safe="/")
    )
    return address, key


def restore_backup():
    """Herstel alleen als de lokale DB ontbreekt."""
    if Path(DB).exists():
        return "Lokale database aanwezig"

    config = storage_config()
    if config is None:
        return "Externe opslag nog niet ingesteld"

    url, key = config
    response = requests.get(
        url,
        headers={"apikey": key},
        timeout=35
    )

    if response.status_code == 404:
        return "Nog geen externe back-up gevonden"

    response.raise_for_status()

    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".db", dir=str(Path(DB).parent)
    ) as tmp:
        tmp.write(response.content)
        temporary = tmp.name

    try:
        with sqlite3.connect(f"file:{temporary}?mode=ro", uri=True) as c:
            status = c.execute("PRAGMA quick_check").fetchone()[0]
            if status != "ok":
                raise sqlite3.DatabaseError(status)
        os.replace(temporary, DB)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return "Supabase-back-up hersteld"


def backup(force=False):
    """Consistente SQLite-kopie uploaden naar private Storage."""
    global LAST_BACKUP

    config = storage_config()
    if config is None:
        return "Geen Supabase-back-up ingesteld"

    if not force and time.monotonic() - LAST_BACKUP < 120:
        return "Recent geback-upt"

    url, key = config

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        with connect() as source:
            with sqlite3.connect(tmp.name) as target:
                source.backup(target)
        data = Path(tmp.name).read_bytes()

    response = requests.post(
        url,
        headers={
            "apikey": key,
            "x-upsert": "true",
            "Content-Type": "application/octet-stream"
        },
        data=data,
        timeout=40
    )
    response.raise_for_status()
    LAST_BACKUP = time.monotonic()
    return f"Back-up opgeslagen: {len(data):,} bytes"


try:
    RESTORE_STATUS = restore_backup()
except (requests.RequestException, sqlite3.Error, OSError, ValueError) as exc:
    st.error(f"Veilig herstellen mislukt: {exc}")
    st.stop()


# ============================================================
# DATABASE: ALLE OUDE TABELLEN BEHOUDEN
# ============================================================

def initialize():
    schema = [
        """CREATE TABLE IF NOT EXISTS discovered (
            address TEXT PRIMARY KEY, name TEXT, symbol TEXT,
            first_seen TEXT, last_seen TEXT)""",
        """CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY, name TEXT, added_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS tracking_state (
            address TEXT PRIMARY KEY, first_seen TEXT,
            last_checked TEXT, last_found TEXT,
            missed_scans INTEGER DEFAULT 0, status TEXT)""",
        """CREATE TABLE IF NOT EXISTS radar_v54 (
            address TEXT NOT NULL, scanned_at TEXT NOT NULL,
            name TEXT, symbol TEXT, pair_address TEXT, source TEXT,
            price REAL, liquidity REAL, volume_m5 REAL,
            buys_m5 INTEGER, sells_m5 INTEGER, age_hours REAL,
            narrative INTEGER, momentum INTEGER, evidence TEXT,
            phase TEXT, warnings TEXT,
            PRIMARY KEY(address,scanned_at))""",
        """CREATE TABLE IF NOT EXISTS paper_v54 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL, name TEXT, pair_address TEXT,
            opened_at TEXT NOT NULL, entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL, amount_usd REAL NOT NULL,
            quantity REAL NOT NULL, fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL, status TEXT DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL, exit_effective REAL,
            pnl_usd REAL, exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL)""",
        """CREATE TABLE IF NOT EXISTS paper_v542_b (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL, name TEXT, pair_address TEXT,
            opened_at TEXT NOT NULL, entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL, amount_usd REAL NOT NULL,
            quantity REAL NOT NULL, fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL, status TEXT DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL, exit_effective REAL,
            pnl_usd REAL, exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL)""",
        """CREATE TABLE IF NOT EXISTS v541_config (
            name TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS v541_runs (
            scanned_at TEXT PRIMARY KEY, finished_at TEXT,
            fetched INTEGER, errors INTEGER,
            entries INTEGER, exits INTEGER, cause TEXT)""",
        """CREATE TABLE IF NOT EXISTS v541_decisions (
            scan_at TEXT, address TEXT, name TEXT, outcome TEXT,
            reason TEXT, price REAL, liquidity REAL,
            PRIMARY KEY(scan_at,address))""",
        """CREATE TABLE IF NOT EXISTS v542_decisions (
            scan_at TEXT, address TEXT, name TEXT, strategy TEXT,
            outcome TEXT, reason TEXT, price REAL, liquidity REAL,
            PRIMARY KEY(scan_at,address,strategy))""",
        """CREATE TABLE IF NOT EXISTS v542_runs (
            started_at TEXT PRIMARY KEY, finished_at TEXT,
            cause TEXT, tokens INTEGER, api_errors INTEGER,
            a_entries INTEGER, b_entries INTEGER,
            a_exits INTEGER, b_exits INTEGER)""",
        """CREATE TABLE IF NOT EXISTS v57_monitor (
            checked_at TEXT PRIMARY KEY, positions INTEGER,
            pairs INTEGER, exits INTEGER, api_errors INTEGER,
            backup_status TEXT)"""
    ]

    paper_columns = {
        "address": "TEXT", "name": "TEXT", "pair_address": "TEXT",
        "opened_at": "TEXT", "entry_market": "REAL",
        "entry_effective": "REAL", "amount_usd": "REAL",
        "quantity": "REAL", "fee_rate": "REAL",
        "slippage_rate": "REAL", "status": "TEXT",
        "closed_at": "TEXT", "exit_market": "REAL",
        "exit_effective": "REAL", "pnl_usd": "REAL",
        "exit_reason": "TEXT", "last_mark": "REAL",
        "last_mark_at": "TEXT", "peak_mark": "REAL"
    }

    migrations = {
        "paper_v54": paper_columns,
        "paper_v542_b": paper_columns,
        "radar_v54": {
            "address": "TEXT", "scanned_at": "TEXT",
            "name": "TEXT", "symbol": "TEXT",
            "pair_address": "TEXT", "source": "TEXT",
            "price": "REAL", "liquidity": "REAL",
            "volume_m5": "REAL", "buys_m5": "INTEGER",
            "sells_m5": "INTEGER", "age_hours": "REAL",
            "narrative": "INTEGER", "momentum": "INTEGER",
            "evidence": "TEXT", "phase": "TEXT", "warnings": "TEXT"
        },
        "v542_runs": {
            "started_at": "TEXT", "finished_at": "TEXT",
            "cause": "TEXT", "tokens": "INTEGER",
            "api_errors": "INTEGER", "a_entries": "INTEGER",
            "b_entries": "INTEGER", "a_exits": "INTEGER",
            "b_exits": "INTEGER"
        },
        "v542_decisions": {
            "scan_at": "TEXT", "address": "TEXT", "name": "TEXT",
            "strategy": "TEXT", "outcome": "TEXT",
            "reason": "TEXT", "price": "REAL", "liquidity": "REAL"
        }
    }

    with connect() as c:
        for statement in schema:
            c.execute(statement)

        for table, columns in migrations.items():
            existing = {
                x[1] for x in c.execute(f'PRAGMA table_info("{table}")')
            }
            for column, kind in columns.items():
                if column not in existing:
                    c.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN "{column}" {kind}'
                    )

        columns = {
            x[1] for x in c.execute('PRAGMA table_info("v542_runs")')
        }
        if "scan_at" in columns:
            c.execute(
                "UPDATE v542_runs SET started_at=scan_at WHERE started_at IS NULL"
            )

        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_history ON radar_v54(address,scanned_at)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_a ON paper_v54(status)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_b ON paper_v542_b(status)"
        )


def get_setting(key, default):
    rows = query("SELECT value FROM v541_config WHERE name=?", (key,))
    return rows[0][0] == "1" if rows else default


def save_setting(key, value):
    with connect() as c:
        c.execute(
            """INSERT INTO v541_config(name,value) VALUES(?,?)
               ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
            (key, "1" if value else "0")
        )


# ============================================================
# DEXSCREENER
# ============================================================

def api_get(path, params=None):
    r = HTTP.get(API + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def discover():
    addresses = {}
    errors = []

    def add(address, source):
        if isinstance(address, str) and ADDRESS.fullmatch(address):
            addresses.setdefault(address, set()).add(source)

    for term in SEARCHES:
        try:
            payload = api_get("/latest/dex/search", {"q": term})
            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                base = pair.get("baseToken") or {}
                title = f"{base.get('name', '')} {base.get('symbol', '')}"
                if match(title, STRONG + RELATED):
                    add(base.get("address"), "DEX search")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Search {term}: {exc}")

    for path in [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]:
        try:
            payload = api_get(path)
            for item in payload if isinstance(payload, list) else []:
                if item.get("chainId") != "solana":
                    continue
                links = " ".join(
                    str(link.get("url", ""))
                    for link in item.get("links") or []
                    if isinstance(link, dict)
                )
                if match(str(item.get("description") or "") + " " + links,
                         STRONG + RELATED):
                    add(item.get("tokenAddress"), "DEX profile")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Profile {path}: {exc}")

    history_queries = [
        "SELECT address FROM paper_v54 WHERE status='OPEN'",
        "SELECT address FROM paper_v542_b WHERE status='OPEN'",
        "SELECT address FROM watchlist ORDER BY added_at DESC",
        """SELECT address FROM tracking_state
           ORDER BY COALESCE(last_checked,first_seen) ASC LIMIT 200""",
        "SELECT address FROM discovered ORDER BY last_seen ASC LIMIT 200"
    ]

    for sql in history_queries:
        for (address,) in query(sql):
            add(address, "Gevolgd")

    protected = {
        x[0] for x in query("SELECT address FROM watchlist")
    }
    for table in ("paper_v54", "paper_v542_b"):
        protected |= {
            x[0] for x in query(
                f"SELECT address FROM {table} WHERE status='OPEN'"
            )
        }

    latest = query(
        """SELECT r.address,r.scanned_at,r.volume_m5
           FROM radar_v54 r
           JOIN (
               SELECT address,MAX(scanned_at) AS last_time
               FROM radar_v54 GROUP BY address
           ) x ON x.address=r.address
              AND x.last_time=r.scanned_at"""
    )

    inactive = set()
    for address, scanned_at, volume in latest:
        minutes = (
            datetime.now(timezone.utc) - parse(scanned_at)
        ).total_seconds() / 60

        if (
            zero(volume) < 25
            and minutes < 60
            and address not in protected
        ):
            inactive.add(address)

    filtered = {
        a: sources
        for a, sources in addresses.items()
        if a not in inactive
    }
    return filtered, errors, len(addresses) - len(filtered)


def market_pairs(addresses):
    found = {}
    errors = []

    for offset in range(0, len(addresses), 30):
        batch = addresses[offset:offset + 30]

        try:
            pairs = api_get(
                "/tokens/v1/solana/" + ",".join(batch)
            )
            if not isinstance(pairs, list):
                raise ValueError("Onverwacht API-formaat")

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address")
                if address not in batch:
                    continue

                old = found.get(address)
                liquidity = zero(
                    (pair.get("liquidity") or {}).get("usd")
                )
                old_liquidity = (
                    zero((old.get("liquidity") or {}).get("usd"))
                    if old else -1
                )

                if liquidity > old_liquidity:
                    found[address] = pair

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"API batch: {exc}")

    return found, errors


# ============================================================
# MOMENTUM — 3 METINGEN
# ============================================================

def history(address, stamp):
    return query(
        """SELECT scanned_at,price,volume_m5,pair_address
           FROM radar_v54 WHERE address=? AND scanned_at<?
           ORDER BY scanned_at DESC LIMIT 16""",
        (address, stamp)
    )


def signals(address, stamp, price, volume, pair):
    if price is None or price <= 0:
        return 0, False, "Prijs ontbreekt", "ONVOLLEDIGE_DATA"
    if volume is None:
        return 0, False, "Volume ontbreekt", "ONVOLLEDIGE_DATA"
    if volume <= 0:
        return 0, False, "5m-volume is $0", "GEEN_HANDEL"

    previous = [
        (t, num(p), num(v), pid)
        for t, p, v, pid in history(address, stamp)
        if num(p) is not None and num(p) > 0
        and num(v) is not None and num(v) > 0
        and pid == pair
    ]

    if len(previous) < 2:
        return (
            0, False,
            f"{len(previous)+1}/3 metingen voor hetzelfde pair",
            "TE_WEINIG_METINGEN"
        )

    recent = next(
        (
            x for x in previous
            if 3.5 <= (
                parse(stamp) - parse(x[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )
    old = next(
        (
            x for x in previous
            if recent and 3.5 <= (
                parse(recent[0]) - parse(x[0])
            ).total_seconds() / 60 <= 20
        ),
        None
    )

    if not recent or not old:
        return (
            0, False,
            "Geen drie geldige metingen met intervallen van 3,5–20 min",
            "ONGELDIG_INTERVAL"
        )

    gain = price / old[1] - 1
    price_up = old[1] < recent[1] < price
    volume_up = old[2] < recent[2] < volume

    strategy_a = (
        price_up and gain >= .02 and volume_up and volume >= 250
    )
    strategy_b = (
        gain >= .05 and price > recent[1] and volume >= 250
    )

    score = 80 if strategy_a else 30 if (price_up or volume_up) else 0

    explanation = (
        f"Koers {gain:+.1%}; "
        f"3 prijzen stijgend={price_up}; "
        f"3 volumes stijgend={volume_up}; "
        f"volume=${volume:,.0f}; "
        f"A={strategy_a}; B={strategy_b}"
    )

    return score, strategy_b, explanation, "GEMETEN"


def analyze(address, pair, sources, stamp):
    base = pair.get("baseToken") or {}
    name = str(base.get("name") or "Onbekend")
    symbol = str(base.get("symbol") or "?")
    title = name + " " + symbol

    narrative = (
        100 if match(title, STRONG)
        else 65 if match(title, RELATED)
        else 0
    )

    created = num(pair.get("pairCreatedAt"))
    age = (
        max(0, (parse(stamp).timestamp() * 1000 - created) / 3600000)
        if created else None
    )

    price = num(pair.get("priceUsd"))
    liquidity = num((pair.get("liquidity") or {}).get("usd"))
    volume = num((pair.get("volume") or {}).get("m5"))
    tx = (pair.get("txns") or {}).get("m5") or {}
    buys, sells = int(zero(tx.get("buys"))), int(zero(tx.get("sells")))
    pair_id = str(pair.get("pairAddress") or "")

    score, b_signal, evidence, diagnostic = signals(
        address, stamp, price, volume, pair_id
    )

    count = buys + sells
    avg = volume / count if count and volume is not None else None
    micro = count >= 100 and avg is not None and avg < 3

    warnings = []
    if liquidity is None or liquidity < 10000:
        warnings.append("Liquiditeit onbekend of te laag")
    if micro:
        warnings.append("Microtransacties")
    warnings.append("Contractveiligheid en verkoopbaarheid niet geverifieerd")

    return {
        "address": address, "name": name, "symbol": symbol,
        "pair": pair_id, "source": ", ".join(sorted(sources)),
        "narrative": narrative, "age": age,
        "price": price, "liquidity": liquidity, "volume": volume,
        "buys": buys, "sells": sells, "micro": micro,
        "score": score, "b_signal": b_signal,
        "evidence": evidence, "diagnostic": diagnostic,
        "warnings": "; ".join(warnings)
    }


def save_observations(records, stamp):
    with connect() as c:
        for r in records:
            c.execute(
                """INSERT INTO discovered
                   (address,name,symbol,first_seen,last_seen)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(address) DO UPDATE SET
                   name=excluded.name,symbol=excluded.symbol,
                   last_seen=excluded.last_seen""",
                (r["address"], r["name"], r["symbol"], stamp, stamp)
            )
            c.execute(
                """INSERT INTO tracking_state
                   (address,first_seen,last_checked,last_found,
                    missed_scans,status)
                   VALUES(?,?,?,?,0,'ACTIEF')
                   ON CONFLICT(address) DO UPDATE SET
                   last_checked=excluded.last_checked,
                   last_found=excluded.last_found,
                   missed_scans=0,status='ACTIEF'""",
                (r["address"], stamp, stamp, stamp)
            )

            phase = (
                "MOMENTUM" if r["score"] == 80
                else "PRE-TREND" if r["age"] is not None and r["age"] < 24
                else "MONITOREN"
            )

            c.execute(
                """INSERT OR REPLACE INTO radar_v54
                   (address,scanned_at,name,symbol,pair_address,
                    source,price,liquidity,volume_m5,buys_m5,
                    sells_m5,age_hours,narrative,momentum,
                    evidence,phase,warnings)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["address"], stamp, r["name"], r["symbol"],
                    r["pair"], r["source"], r["price"], r["liquidity"],
                    r["volume"], r["buys"], r["sells"], r["age"],
                    r["narrative"], r["score"], r["evidence"],
                    phase, r["warnings"]
                )
            )


# ============================================================
# VIRTUELE STRATEGIEËN
# ============================================================

def decide(r, strategy, settings, used):
    if r["address"] in used:
        return "GEEN_INSTAP", "Token eerder virtueel verhandeld"
    if r["narrative"] < 65:
        return "GEEN_INSTAP", "Geen GTA-narrative"
    if r["diagnostic"] != "GEMETEN":
        return "GEEN_INSTAP", r["diagnostic"] + ": " + r["evidence"]
    if strategy == "A" and r["score"] != 80:
        return "GEEN_INSTAP", "A: onvoldoende momentum. " + r["evidence"]
    if strategy == "B" and not r["b_signal"]:
        return "GEEN_INSTAP", "B: onvoldoende momentum. " + r["evidence"]
    if r["price"] is None or r["price"] <= 0:
        return "GEBLOKKEERD", "Prijs ontbreekt"
    if r["liquidity"] is None or r["liquidity"] < settings["min_liq"]:
        return "GEBLOKKEERD", "Onvoldoende liquiditeit"
    if r["age"] is None or r["age"] > 24:
        return "GEBLOKKEERD", "Pair te oud of leeftijd onbekend"
    if r["volume"] is None or r["volume"] < 250:
        return "GEBLOKKEERD", "Onvoldoende volume"
    if r["buys"] <= r["sells"]:
        return "GEBLOKKEERD", "Kooptransacties niet groter dan verkopen"
    if strategy == "A" and r["micro"]:
        return "GEBLOKKEERD", "Microtransacties geblokkeerd door A"
    if strategy == "B" and r["micro"]:
        return "KANDIDAAT", "Experimenteel: microtransacties toegestaan"
    return "KANDIDAAT", "Voorwaarden gehaald, contractrisico onbekend"


def close_open_positions(pairs, stamp, settings):
    """Gebruikt alleen marktdata van dezelfde token én hetzelfde pair."""
    exits = {"A": 0, "B": 0}

    with connect() as c:
        for strategy, table in [
            ("A", "paper_v54"), ("B", "paper_v542_b")
        ]:
            trades = c.execute(
                f"""SELECT id,address,pair_address,opened_at,
                    entry_market,quantity,amount_usd,
                    fee_rate,slippage_rate
                    FROM {table} WHERE status='OPEN'"""
            ).fetchall()

            for trade in trades:
                (
                    trade_id, address, original_pair, opened,
                    entry, quantity, amount, fee, slip
                ) = trade

                pair = pairs.get(address)
                if not pair:
                    continue

                current_pair = str(pair.get("pairAddress") or "")
                if original_pair and original_pair != current_pair:
                    c.execute(
                        f"""UPDATE {table} SET
                            exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                            WHERE id=? AND status='OPEN'""",
                        (trade_id,)
                    )
                    continue

                price = num(pair.get("priceUsd"))
                liquidity = num((pair.get("liquidity") or {}).get("usd"))

                if price is None or price <= 0:
                    continue

                c.execute(
                    f"""UPDATE {table}
                        SET last_mark=?,last_mark_at=?,
                        peak_mark=MAX(COALESCE(peak_mark,0),?)
                        WHERE id=? AND status='OPEN'""",
                    (price, stamp, price, trade_id)
                )

                gain = price / entry - 1
                hours = (parse(stamp) - parse(opened)).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT" if gain >= settings["take"]
                    else "STOP_LOSS" if gain <= -settings["stop"]
                    else "TIME_EXIT" if hours >= settings["max_hours"]
                    else None
                )

                if not reason:
                    continue

                if liquidity is None or liquidity < settings["min_liq"]:
                    c.execute(
                        f"""UPDATE {table}
                            SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                            WHERE id=? AND status='OPEN'""",
                        (trade_id,)
                    )
                    continue

                effective = price * (1 - zero(slip))
                pnl = quantity * effective * (1 - zero(fee)) - amount

                result = c.execute(
                    f"""UPDATE {table}
                        SET status='CLOSED',closed_at=?,
                        exit_market=?,exit_effective=?,pnl_usd=?,
                        exit_reason=?
                        WHERE id=? AND status='OPEN'""",
                    (
                        stamp, price, effective, pnl, reason, trade_id
                    )
                )
                exits[strategy] += result.rowcount

    return exits


def create_entries(records, stamp, settings, active_a, active_b):
    entries = {"A": 0, "B": 0}

    for strategy, table, enabled in [
        ("A", "paper_v54", active_a),
        ("B", "paper_v542_b", active_b)
    ]:
        with connect() as c:
            used = {
                x[0] for x in c.execute(
                    f"SELECT DISTINCT address FROM {table}"
                )
            }
            opened = c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'"
            ).fetchone()[0]

            for r in records:
                outcome, reason = decide(r, strategy, settings, used)

                if outcome == "KANDIDAAT" and not enabled:
                    outcome, reason = "UITGESCHAKELD", "Strategie staat uit"

                elif outcome == "KANDIDAAT" and opened >= settings["max_positions"]:
                    outcome, reason = "GEBLOKKEERD", "Maximaal aantal posities"

                elif outcome == "KANDIDAAT":
                    effective = r["price"] * (1 + settings["slip"])
                    quantity = (
                        settings["amount"] * (1 - settings["fee"])
                        / effective
                    )

                    c.execute(
                        f"""INSERT INTO {table}
                            (address,name,pair_address,opened_at,
                             entry_market,entry_effective,
                             amount_usd,quantity,fee_rate,
                             slippage_rate,status,last_mark,
                             last_mark_at,peak_mark)
                            VALUES(?,?,?,?,?,?,?,?,?,?,
                                   'OPEN',?,?,?)""",
                        (
                            r["address"], r["name"], r["pair"],
                            stamp, r["price"], effective,
                            settings["amount"], quantity,
                            settings["fee"], settings["slip"],
                            r["price"], stamp, r["price"]
                        )
                    )

                    used.add(r["address"])
                    opened += 1
                    entries[strategy] += 1
                    outcome = "PAPER_ENTRY"
                    reason = "Virtuele instap, geen echte order"

                c.execute(
                    """INSERT OR REPLACE INTO v542_decisions
                       (scan_at,address,name,strategy,
                        outcome,reason,price,liquidity)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        stamp, r["address"], r["name"], strategy,
                        outcome, reason, r["price"], r["liquidity"]
                    )
                )

    return entries


# ============================================================
# MARKTSCAN — ELKE 5 MINUTEN
# ============================================================

def full_scan(trigger, settings, active_a, active_b):
    with LOCK:
        stamp = now()

        addresses, errors, skipped = discover()
        pairs, pair_errors = market_pairs(list(addresses))
        errors.extend(pair_errors)

        open_addresses = set()
        for table in ("paper_v54", "paper_v542_b"):
            open_addresses |= {
                x[0] for x in query(
                    f"SELECT address FROM {table} WHERE status='OPEN'"
                )
            }

        records = []
        for address, pair in pairs.items():
            try:
                r = analyze(address, pair, addresses[address], stamp)
                if r["narrative"] >= 65 or address in open_addresses:
                    records.append(r)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"Analyse {address[:10]}: {exc}")

        save_observations(records, stamp)

        exits = close_open_positions(pairs, stamp, settings)
        entries = create_entries(
            records, stamp, settings, active_a, active_b
        )

        finished = now()
        with connect() as c:
            c.execute(
                """INSERT INTO v542_runs
                   (started_at,finished_at,cause,tokens,
                    api_errors,a_entries,b_entries,a_exits,b_exits)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    stamp, finished, trigger, len(records), len(errors),
                    entries["A"], entries["B"],
                    exits["A"], exits["B"]
                )
            )

        try:
            backup(force=True)
        except (requests.RequestException, OSError, sqlite3.Error) as exc:
            errors.append("Backup mislukt: " + str(exc))

        return len(records), skipped, entries, exits, errors


# ============================================================
# PAPERTRADE-MONITOR — ELKE 15 SECONDEN
# ============================================================

def monitor_positions(settings):
    if not LOCK.acquire(blocking=False):
        return None

    try:
        addresses = set()

        for table in ("paper_v54", "paper_v542_b"):
            addresses |= {
                x[0] for x in query(
                    f"SELECT address FROM {table} WHERE status='OPEN'"
                )
            }

        if not addresses:
            return {
                "open": 0, "checked": 0, "exits": 0,
                "errors": [], "backup": "-"
            }

        pairs, errors = market_pairs(sorted(addresses))
        stamp = now()

        exits = close_open_positions(pairs, stamp, settings)
        total_exits = exits["A"] + exits["B"]

        with connect() as c:
            c.execute(
                """INSERT OR REPLACE INTO v57_monitor
                   (checked_at,positions,pairs,exits,
                    api_errors,backup_status)
                   VALUES(?,?,?,?,?,?)""",
                (
                    stamp, len(addresses), len(pairs),
                    total_exits, len(errors), "PENDING"
                )
            )

        try:
            status = backup(force=total_exits > 0)
        except (requests.RequestException, OSError, sqlite3.Error) as exc:
            status = "BACKUP ERROR: " + str(exc)
            errors.append(status)

        with connect() as c:
            c.execute(
                """UPDATE v57_monitor SET backup_status=?
                   WHERE checked_at=?""",
                (status, stamp)
            )

        return {
            "open": len(addresses),
            "checked": len(pairs),
            "exits": total_exits,
            "errors": errors,
            "backup": status
        }

    finally:
        LOCK.release()


# ============================================================
# DATA VOOR HET DASHBOARD
# ============================================================

def latest_tokens():
    last = query(
        "SELECT started_at FROM v542_runs ORDER BY started_at DESC LIMIT 1"
    )
    if not last:
        return pd.DataFrame()

    rows = query(
        """SELECT name,symbol,address,pair_address,
           age_hours,price,liquidity,volume_m5,
           buys_m5,sells_m5,momentum,evidence,warnings
           FROM radar_v54 WHERE scanned_at=?""",
        (last[0][0],)
    )

    return pd.DataFrame(rows, columns=[
        "Naam", "Symbool", "Adres", "Pair",
        "Leeftijd uur", "Prijs $", "Liquiditeit $",
        "5m-volume $", "Buys", "Sells", "A momentum",
        "Trend", "Waarschuwingen"
    ])


def trade_table(table):
    rows = query(
        f"""SELECT id,name,address,opened_at,amount_usd,
            entry_market,last_mark,status,pnl_usd,exit_reason
            FROM {table} ORDER BY id DESC LIMIT 300"""
    )

    df = pd.DataFrame(rows, columns=[
        "ID", "Naam", "Adres", "Open UTC",
        "Inzet $", "Instap $", "Laatste prijs $",
        "Status", "Gerealiseerd P/L $", "Exitreden"
    ])

    if not df.empty:
        df["Geopend België"] = df["Open UTC"].apply(belgian_time)
        df = df.drop(columns=["Open UTC"])

    return df


def open_risk(table):
    rows = query(
        f"""SELECT id,name,address,amount_usd,quantity,
            fee_rate,slippage_rate,last_mark,last_mark_at,
            exit_reason FROM {table}
            WHERE status='OPEN' ORDER BY id DESC"""
    )
    result = []

    for trade in rows:
        trade_id, name, address, stake, qty, fee, slip, mark, last, reason = trade

        sale = (
            qty * mark * (1 - zero(slip)) * (1 - zero(fee))
            if mark is not None and qty is not None
            else None
        )
        pnl = sale - stake if sale is not None else None
        net = 100 * pnl / stake if pnl is not None and stake else None
        age = (
            (datetime.now(timezone.utc) - parse(last)).total_seconds() / 60
            if last else None
        )

        result.append({
            "ID": trade_id,
            "Naam": name,
            "Adres": address,
            "Laatste koers": mark,
            "Netto mark P/L $": pnl,
            "Netto mark P/L %": net,
            "Laatste prijscheck (min geleden)": age,
            "Waarschuwing": reason or (
                "VEROUDERDE PRIJS" if age is None or age > 1
                else "Verkoopbaarheid niet gecontroleerd"
            )
        })

    return pd.DataFrame(result)


# ============================================================
# LEARNING COLLECTOR (LEZEN, NIET SCHRIJVEN)
# ============================================================

def learning_panel():
    st.subheader("🧠 Learning Engine")
    st.caption(
        "GitHub Learning Collector blijft gescheiden van de radardatabase."
    )

    uploaded_json = st.file_uploader(
        "Upload data/latest_scan.json",
        type=["json"],
        key="learning_json"
    )

    if uploaded_json:
        try:
            payload = json.load(uploaded_json)
            if not isinstance(payload, dict):
                raise ValueError("JSON-object verwacht")

            cols = st.columns(4)
            keys = [
                "discovered", "recorded",
                "total_snapshots", "total_labels"
            ]
            labels = [
                "Ontdekt", "Deze run",
                "Snapshots", "Uitkomsten"
            ]

            for col, key, label in zip(cols, keys, labels):
                col.metric(label, payload.get(key, "-"))

            if payload.get("errors"):
                st.warning(str(payload["errors"][:5]))

        except (ValueError, UnicodeError) as exc:
            st.error(str(exc))

    uploaded_db = st.file_uploader(
        "Upload data/learning.db",
        type=["db"],
        key="learning_db"
    )

    if uploaded_db:
        try:
            if uploaded_db.size > 100_000_000:
                st.error("Database groter dan 100 MB")
                return

            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                tmp.write(uploaded_db.getvalue())
                tmp.flush()

                with sqlite3.connect(
                    "file:" + tmp.name + "?mode=ro&immutable=1",
                    uri=True
                ) as c:
                    snapshots = c.execute(
                        "SELECT COUNT(*) FROM snapshots"
                    ).fetchone()[0]
                    outcomes = c.execute(
                        "SELECT COUNT(*) FROM outcomes"
                    ).fetchone()[0]

                    c1, c2 = st.columns(2)
                    c1.metric("Snapshots", snapshots)
                    c2.metric("Uitkomsten", outcomes)

                    df = pd.read_sql_query(
                        """SELECT name,token_address,pair_address,
                           scanned_at,price_usd,liquidity_usd,volume_m5
                           FROM snapshots ORDER BY scanned_at DESC LIMIT 150""",
                        c
                    )
                    st.dataframe(df, hide_index=True, use_container_width=True)

        except (sqlite3.Error, ValueError) as exc:
            st.error("Learning-DB fout: " + str(exc))


# ============================================================
# STREAMLIT INTERFACE
# ============================================================

initialize()

st.title("🎮 GTA 6 Radar V5.7")
st.caption(
    "Actieve tokens eerst · Strategie A/B · "
    "15 seconden papertrade-monitor · Supabase-back-up"
)
st.warning(
    "Alle transacties zijn virtueel. Een stoploss wordt alleen "
    "gesimuleerd op beschikbare marktdata en garandeert geen "
    "werkelijke uitvoerbaarheid."
)

if "v542_auto" not in st.session_state:
    st.session_state.v542_auto = get_setting("auto_scan", True)
if "v542_a" not in st.session_state:
    st.session_state.v542_a = get_setting("paper", False)
if "v542_b" not in st.session_state:
    st.session_state.v542_b = get_setting("paper_b", False)


def change_auto():
    save_setting("auto_scan", st.session_state.v542_auto)


def change_a():
    save_setting("paper", st.session_state.v542_a)


def change_b():
    save_setting("paper_b", st.session_state.v542_b)


st.sidebar.header("⚙️ Instellingen")

if storage_config():
    st.sidebar.success("Supabase Secret Key geconfigureerd")
else:
    st.sidebar.warning("Nog geen externe back-up ingesteld")

st.sidebar.caption(RESTORE_STATUS)

st.sidebar.toggle(
    "Automatische marktscan",
    key="v542_auto", on_change=change_auto
)
st.sidebar.toggle(
    "Strategie A: papertrading",
    key="v542_a", on_change=change_a
)
st.sidebar.toggle(
    "Strategie B: papertrading",
    key="v542_b", on_change=change_b
)

interval = st.sidebar.selectbox(
    "Marktscan iedere ... minuten", [5, 10, 15]
)
minimum_liq = st.sidebar.number_input(
    "Minimale liquiditeit ($)", 1000.0, 1000000.0, 10000.0, 1000.0
)
amount = st.sidebar.number_input(
    "Virtuele inzet ($)", 5.0, 1000.0, 25.0, 5.0
)
max_positions = st.sidebar.number_input(
    "Max. posities per strategie", 1, 20, 3
)
fee = st.sidebar.number_input(
    "Fee per zijde (%)", 0.0, 10.0, 1.0, 0.25
)
slip = st.sidebar.number_input(
    "Slippage per zijde (%)", 0.0, 30.0, 2.0, 0.5
)
take = st.sidebar.number_input(
    "Take profit (%)", 5, 500, 30, 5
)
stop = st.sidebar.number_input(
    "Stop loss (%)", 5, 95, 15, 5
)
max_hours = st.sidebar.number_input(
    "Maximale looptijd (uur)", 1, 72, 4
)

settings = {
    "min_liq": minimum_liq,
    "amount": amount,
    "max_positions": max_positions,
    "fee": fee / 100,
    "slip": slip / 100,
    "take": take / 100,
    "stop": stop / 100,
    "max_hours": max_hours
}


@st.fragment(run_every="15s")
def paper_monitor():
    st.subheader("⚡ Papertrade-monitor — 15 seconden")
    result = monitor_positions(settings)

    if result is None:
        st.info("De marktscan is bezig; deze controle wordt overgeslagen.")
    elif result["open"] == 0:
        st.info("Geen open papertrades om te controleren.")
    else:
        a, b, c = st.columns(3)
        a.metric("Open tokens", result["open"])
        b.metric("Prijsdata ontvangen", result["checked"])
        c.metric("Nieuwe virtuele exits", result["exits"])

        if result["errors"]:
            st.warning(" | ".join(result["errors"][:3]))

    rows = query(
        """SELECT checked_at,positions,pairs,exits,backup_status
           FROM v57_monitor ORDER BY checked_at DESC LIMIT 5"""
    )
    if rows:
        st.dataframe(
            pd.DataFrame(rows, columns=[
                "Tijd UTC", "Posities", "Pairs", "Exits", "Back-up"
            ]),
            hide_index=True,
            use_container_width=True
        )


@st.fragment(run_every="60s")
def dashboard():
    manual = st.button("🔄 Scan nu", type="primary")

    last = query(
        """SELECT started_at,finished_at,cause
           FROM v542_runs ORDER BY started_at DESC LIMIT 1"""
    )

    last_end = parse(last[0][1]) if last and last[0][1] else None
    current = datetime.now(timezone.utc)
    next_scan = (
        last_end + timedelta(minutes=interval)
        if last_end else current
    )

    st.subheader("🕒 Scanplanning")
    st.write(
        "Laatste scan:",
        belgian_time(last[0][1]) if last else "Nog geen scan"
    )
    st.write(
        "Volgende scan:",
        belgian_time(next_scan.isoformat())
        if st.session_state.v542_auto else "Automatisch uit"
    )

    due = st.session_state.v542_auto and current >= next_scan

    if manual or due:
        try:
            with st.spinner("Tokens analyseren..."):
                count, skipped, entries, exits, errors = full_scan(
                    "MANUEEL" if manual else "AUTOMATISCH",
                    settings,
                    st.session_state.v542_a,
                    st.session_state.v542_b
                )

            st.success(
                f"{count} tokens onderzocht · "
                f"{skipped} inactieve tokens overgeslagen · "
                f"A: {entries['A']} paperinstappen · "
                f"B: {entries['B']} paperinstappen"
            )

            if errors:
                with st.expander(f"{len(errors)} meldingen"):
                    st.write(errors[:30])

        except (
            requests.RequestException,
            sqlite3.Error,
            ValueError,
            TypeError
        ) as exc:
            st.error("Scanfout: " + str(exc))

    tokens = latest_tokens()

    a_open = query(
        "SELECT COUNT(*) FROM paper_v54 WHERE status='OPEN'"
    )[0][0]
    b_open = query(
        "SELECT COUNT(*) FROM paper_v542_b WHERE status='OPEN'"
    )[0][0]

    x, y, z, w = st.columns(4)
    x.metric("Tokens laatste scan", len(tokens))
    y.metric(
        "A-momentumsignalen",
        int((tokens["A momentum"] == 80).sum()) if not tokens.empty else 0
    )
    z.metric("A open posities", a_open)
    w.metric("B open posities", b_open)

    st.subheader("📡 Actieve GTA 6-tokens")

    if tokens.empty:
        st.info("Nog geen scanresultaten.")
    else:
        active = tokens[
            tokens["5m-volume $"].fillna(0) >= 25
        ].copy()
        quiet = tokens[
            tokens["5m-volume $"].fillna(0) < 25
        ].copy()

        if not active.empty:
            st.dataframe(
                active.sort_values("5m-volume $", ascending=False),
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("Geen tokens boven $25 vijfminutenvolume.")

        with st.expander(f"💤 Inactieve tokens ({len(quiet)})"):
            st.dataframe(
                quiet, hide_index=True, use_container_width=True
            )

        st.download_button(
            "📥 Tokenmetingen CSV",
            tokens.to_csv(index=False).encode("utf-8"),
            "gta6_v57_tokens.csv",
            "text/csv"
        )

        choices = {
            f"{row['Naam']} · {row['Adres'][:12]}": row["Adres"]
            for _, row in tokens.iterrows()
        }

        choice = st.selectbox("Tokenhistoriek", list(choices))
        address = choices[choice]

        st.code(address, language="text")
        st.link_button(
            "Bekijk DexScreener",
            "https://dexscreener.com/solana/" + address
        )

        if st.button("⭐ Voeg toe aan watchlist"):
            name = tokens[tokens["Adres"] == address].iloc[0]["Naam"]
            with connect() as c:
                c.execute(
                    """INSERT OR IGNORE INTO watchlist
                       (address,name,added_at) VALUES(?,?,?)""",
                    (address, name, now())
                )
            st.success("Toegevoegd!")

        hist = query(
            """SELECT scanned_at,price,volume_m5
               FROM radar_v54 WHERE address=?
               ORDER BY scanned_at DESC LIMIT 120""",
            (address,)
        )

        if len(hist) >= 2:
            hd = pd.DataFrame(
                hist, columns=["Tijd", "Prijs $", "5m-volume $"]
            )
            hd["Tijd"] = pd.to_datetime(
                hd["Tijd"], utc=True
            ).dt.tz_convert("Europe/Brussels")

            hd = hd.sort_values("Tijd").set_index("Tijd")
            st.line_chart(hd[["Prijs $"]])
            st.line_chart(hd[["5m-volume $"]])

    st.subheader("🔎 Beslissingen Strategie A en B")

    newest = query(
        "SELECT started_at FROM v542_runs ORDER BY started_at DESC LIMIT 1"
    )

    if newest:
        rows = query(
            """SELECT name,address,strategy,outcome,reason,price,liquidity
               FROM v542_decisions WHERE scan_at=?
               ORDER BY strategy,outcome,name""",
            (newest[0][0],)
        )

        decisions = pd.DataFrame(rows, columns=[
            "Naam", "Adres", "Strategie", "Uitkomst",
            "Reden", "Prijs $", "Liquiditeit $"
        ])
        chosen = st.radio(
            "Strategie", ["Allebei", "A", "B"], horizontal=True
        )
        displayed = (
            decisions if chosen == "Allebei"
            else decisions[decisions["Strategie"] == chosen]
        )
        st.dataframe(
            displayed, hide_index=True, use_container_width=True
        )

        st.download_button(
            "📥 Beslissingen CSV",
            decisions.to_csv(index=False).encode("utf-8"),
            "gta6_v57_decisions.csv",
            "text/csv"
        )

    st.subheader("🧪 Paperportfolio's")

    a_tab, b_tab = st.tabs([
        "Strategie A — streng",
        "Strategie B — experimenteel"
    ])

    for tab, table in [
        (a_tab, "paper_v54"),
        (b_tab, "paper_v542_b")
    ]:
        with tab:
            trades = trade_table(table)

            if trades.empty:
                st.info("Nog geen virtuele posities in deze strategie.")
            else:
                closed = trades[trades["Status"] == "CLOSED"]
                c1, c2, c3 = st.columns(3)

                c1.metric("Totaal trades", len(trades))
                c2.metric(
                    "Gerealiseerd P/L",
                    f"${closed['Gerealiseerd P/L $'].sum():,.2f}"
                )
                c3.metric(
                    "Winrate",
                    f"{(closed['Gerealiseerd P/L $'] > 0).mean() * 100:.1f}%"
                    if not closed.empty else "-"
                )

                st.dataframe(
                    trades, hide_index=True, use_container_width=True
                )
                st.download_button(
                    "📥 Trades CSV",
                    trades.to_csv(index=False).encode("utf-8"),
                    f"{table}.csv",
                    "text/csv"
                )

    st.subheader("🚨 Open paperposities en risico")

    for label, table in [
        ("Strategie A", "paper_v54"),
        ("Strategie B", "paper_v542_b")
    ]:
        risk = open_risk(table)
        if not risk.empty:
            st.write("**" + label + "**")
            st.dataframe(
                risk, hide_index=True, use_container_width=True
            )

    with st.expander("🧠 Learning Collector — dataset onderzoeken"):
        learning_panel()

    st.subheader("🕒 Scanlog — Belgische tijd")

    logs = query(
        """SELECT started_at,finished_at,cause,tokens,
           api_errors,a_entries,b_entries,a_exits,b_exits
           FROM v542_runs ORDER BY started_at DESC LIMIT 40"""
    )

    if logs:
        df = pd.DataFrame(logs, columns=[
            "Begin UTC", "Einde UTC", "Trigger", "Tokens",
            "API-fouten", "A instappen", "B instappen",
            "A exits", "B exits"
        ])
        df["Begin België"] = df["Begin UTC"].apply(belgian_time)
        df["Einde België"] = df["Einde UTC"].apply(belgian_time)
        st.dataframe(
            df.drop(columns=["Begin UTC", "Einde UTC"]),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# START
# ============================================================

paper_monitor()
dashboard()

st.caption(
    "GTA 6 Radar V5.7 — uitsluitend virtuele handel. "
    "De monitor controleert ongeveer elke 15 seconden zolang "
    "de Streamlit-sessie actief is. Geen gegarandeerde 24/7-monitoring "
    "of echte orderuitvoering."
)
