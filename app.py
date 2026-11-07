"""GTA6 Radar V5.8.1: scanner, recovery and separate wallet backups."""
import os
import json
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="GTA 6 Radar V5.8.1", page_icon="🎮", layout="wide")
# Stable absolute path across Streamlit full reruns and independent fragments.
# A relative override is anchored to this app's own directory, never process CWD.
_APP_DIR = Path(__file__).resolve().parent
_DB_CONFIG = Path(os.getenv("GTA6_RADAR_DB", "gta6_radar.db")).expanduser()
DB = str((_DB_CONFIG if _DB_CONFIG.is_absolute() else _APP_DIR / _DB_CONFIG).resolve())
API = "https://api.dexscreener.com"
LOCAL = ZoneInfo("Europe/Brussels")
SEARCHES = ["GTA6", "GTA VI", "Grand Theft Auto", "Vice City", "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn"]
STRONG = ["gta6", "gta 6", "gta vi", "grand theft auto", "grandtheftauto", "gtavi"]
RELATED = ["vice city", "leonida", "lucia camino", "jason duval", "port gellhorn", "raul bautista"]
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "GTA6RadarResearch/5.7.1", "Accept": "application/json"})
_LOCK = threading.RLock()
_LAST_BACKUP = 0.0


def secret(key, default=""):
    if os.getenv(key):
        return os.environ[key]
    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, AttributeError, KeyError):
        return default


def _storage_config():
    host = secret("SUPABASE_URL").rstrip("/")
    key = secret("SUPABASE_SECRET_KEY")
    bucket = secret("SUPABASE_BACKUP_BUCKET", "gta6-private-backups")
    object_name = secret("SUPABASE_BACKUP_OBJECT", "radar/gta6_radar.db").lstrip("/")
    if not host or not key:
        return None
    if not host.startswith("https://") or "/rest/v1" in host or not key.startswith("sb_secret_"):
        raise RuntimeError("Gebruik de Supabase Project URL zonder /rest/v1 en een sb_secret_ sleutel")
    return host, key, bucket, object_name


def remote_settings():
    """Backward compatible status check (never exposes credentials)."""
    return _storage_config()


def _object_url(cfg, object_name):
    host, key, bucket, _ = cfg
    return host + "/storage/v1/object/" + quote(bucket, safe="") + "/" + quote(object_name, safe="/")


def _get_object(cfg, object_name):
    response = requests.get(_object_url(cfg, object_name), headers={"apikey": cfg[1]}, timeout=40)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _upload_object(cfg, object_name, data, overwrite=False):
    headers = {"apikey": cfg[1], "Content-Type": "application/octet-stream"}
    if overwrite:
        headers["x-upsert"] = "true"
    response = requests.post(_object_url(cfg, object_name), headers=headers, data=data, timeout=60)
    response.raise_for_status()


def _inspect_database(blob):
    """Inspect SQLite without modifying it. Count only ever-growing tables."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        tmp.write(blob)
        tmp.flush()
        with sqlite3.connect("file:" + tmp.name + "?mode=ro", uri=True) as c:
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("SQLite-integriteitscontrole mislukt")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            must_have = ("paper_v54", "paper_v542_b", "radar_v54", "v542_runs")
            if not set(must_have).issubset(tables):
                raise sqlite3.DatabaseError("Essentiële radartabellen ontbreken")
            stats = {t: c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in must_have}
            # Timestamp guard catches a stale local DB with identical row counts.
            stats["last_scan"] = c.execute("SELECT MAX(started_at) FROM v542_runs").fetchone()[0] or ""
            stats["last_trade_a"] = c.execute("SELECT MAX(opened_at) FROM paper_v54").fetchone()[0] or ""
            stats["last_trade_b"] = c.execute("SELECT MAX(opened_at) FROM paper_v542_b").fetchone()[0] or ""
            return stats


def _snapshot():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        with sqlite3.connect(Path(DB).as_uri() + "?mode=ro", uri=True, timeout=30) as source:
            with sqlite3.connect(tmp.name) as destination:
                source.backup(destination)
        return Path(tmp.name).read_bytes()


def backup_restore_at_start():
    """Validate local SQLite before reuse; recover ONLY a verified Supabase copy.

    Never accept an existing empty/incomplete file as a valid portfolio.
    Never silently erase local trade history in favor of an older remote copy.
    """
    required = {"paper_v54", "paper_v542_b", "radar_v54", "v542_runs", "v541_config", "v542_decisions", "v57_monitor"}
    local_path = Path(DB)

    def inspect_file(path):
        if not path.is_file() or path.stat().st_size == 0:
            raise sqlite3.DatabaseError("Database ontbreekt of is leeg")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("SQLite-integriteitscontrole mislukt")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing = required - tables
            if missing:
                raise sqlite3.DatabaseError(
                    "Essentiële tabellen ontbreken: " + ", ".join(sorted(missing))
                )
            stats = {name: conn.execute(
                'SELECT COUNT(*) FROM "' + name + '"'
            ).fetchone()[0] for name in required}
            stats["last_scan"] = conn.execute(
                "SELECT MAX(started_at) FROM v542_runs"
            ).fetchone()[0] or ""
            stats["last_trade_a"] = conn.execute(
                "SELECT MAX(opened_at) FROM paper_v54"
            ).fetchone()[0] or ""
            stats["last_trade_b"] = conn.execute(
                "SELECT MAX(opened_at) FROM paper_v542_b"
            ).fetchone()[0] or ""
            return stats

    with _LOCK:
        try:
            inspect_file(local_path)
        except (sqlite3.Error, OSError, ValueError) as local_error:
            config = _storage_config()
            if config is None:
                raise RuntimeError(
                    "Lokale database onvolledig (" + str(local_error)
                    + "); Supabase-back-up niet ingesteld. "
                    "Herstel geblokkeerd om papertrades te beschermen."
                ) from local_error

            remote = _get_object(config, config[3])
            if remote is None:
                raise RuntimeError(
                    "Lokale database onvolledig (" + str(local_error)
                    + "); geen actieve Supabase-back-up. Niets overschreven."
                ) from local_error
            # Verify source with the project's original portfolio inspector.
            remote_stats = _inspect_database(remote)
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # Important: an incomplete local database can still contain more
            # papertrades than the remote one. Never erase such history.
            if local_path.is_file() and local_path.stat().st_size:
                try:
                    with sqlite3.connect(
                        local_path.resolve().as_uri() + "?mode=ro", uri=True
                    ) as conn:
                        names = {r[0] for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )}
                        for table in ("paper_v54", "paper_v542_b", "radar_v54", "v542_runs"):
                            if table not in names:
                                continue
                            count = conn.execute(
                                'SELECT COUNT(*) FROM "' + table + '"'
                            ).fetchone()[0]
                            if count > remote_stats[table]:
                                raise RuntimeError(
                                    f"Herstel geblokkeerd: lokale {table} heeft "
                                    f"{count} regels, Supabase slechts {remote_stats[table]}."
                                )
                        for table, column, stat in (
                            ("v542_runs", "started_at", "last_scan"),
                            ("paper_v54", "opened_at", "last_trade_a"),
                            ("paper_v542_b", "opened_at", "last_trade_b"),
                        ):
                            if table in names:
                                cols = {r[1] for r in conn.execute(
                                    'PRAGMA table_info("' + table + '")'
                                )}
                                if column in cols:
                                    local_latest = conn.execute(
                                        'SELECT MAX("' + column + '") FROM "' + table + '"'
                                    ).fetchone()[0] or ""
                                    if local_latest > remote_stats[stat]:
                                        raise RuntimeError(
                                            f"Herstel geblokkeerd: lokaal {table} is "
                                            "nieuwer dan de Supabase-back-up."
                                        )
                except sqlite3.Error as exc:
                    raise RuntimeError(
                        "Lokale gegevens zijn niet veilig te vergelijken; "
                        "geen automatische overschrijving: " + str(exc)
                    ) from exc

            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".db", prefix="gta6_restore_",
                    dir=str(local_path.parent), delete=False,
                ) as tmp:
                    tmp.write(remote)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    temporary_path = Path(tmp.name)
                inspect_file(temporary_path)

                if local_path.exists():
                    # Preserve malformed local DB for manual investigation.
                    import shutil
                    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    preserved = local_path.with_name(
                        local_path.name + ".before_recovery_" + suffix
                        + "_" + uuid4().hex[:8]
                    )
                    shutil.copy2(local_path, preserved)

                os.replace(str(temporary_path), str(local_path))
                temporary_path = None
            finally:
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()
            inspect_file(local_path)
            return (
                "Hersteld uit gecontroleerde Supabase-back-up · "
                f"A={remote_stats['paper_v54']} · B={remote_stats['paper_v542_b']}"
            )
        else:
            return "Lokale SQLite-structuur gecontroleerd; historie behouden"

def backup_remote(force=False):
    """Snapshot, verify remote monotonic history, immutable version, then active copy."""
    global _LAST_BACKUP
    cfg = _storage_config()
    if cfg is None:
        raise RuntimeError("Supabase-back-up niet geconfigureerd")
    if not force and time.monotonic() - _LAST_BACKUP < 120:
        return "Recent geback-upt"
    with _LOCK:
        if not Path(DB).exists():
            raise RuntimeError("Lokale database ontbreekt: upload geblokkeerd")
        blob = _snapshot()
        local = _inspect_database(blob)
        previous_blob = _get_object(cfg, cfg[3])
        if previous_blob is not None:
            previous = _inspect_database(previous_blob)
            for key in ("paper_v54", "paper_v542_b", "radar_v54", "v542_runs"):
                if local[key] < previous[key]:
                    raise RuntimeError("BACKUP GEBLOKKEERD: {} lokaal={} extern={}".format(key, local[key], previous[key]))
            for key in ("last_scan", "last_trade_a", "last_trade_b"):
                if local[key] < previous[key]:
                    raise RuntimeError("BACKUP GEBLOKKEERD: lokale geschiedenis ouder ({})".format(key))
        # Unique versions: never overwrite a historical version filename.
        prefix = cfg[3].rsplit("/", 1)[0] if "/" in cfg[3] else "radar"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        version = "{}/versions/{}_{}.db".format(prefix, timestamp, uuid4().hex[:8])
        _upload_object(cfg, version, blob, overwrite=False)
        _upload_object(cfg, cfg[3], blob, overwrite=True)
        _LAST_BACKUP = time.monotonic()
        return "BACKUP OK · {} bytes · {}".format(len(blob), version)


try:
    BACKUP_BOOT = backup_restore_at_start()
except (requests.RequestException, sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
    st.error("Veilig herstel mislukt: " + str(exc))
    st.stop()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def be_time(value):
    return parse_time(value).astimezone(LOCAL).strftime("%d-%m-%Y %H:%M:%S") if value else "-"


def number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def zero(value):
    return number(value) or 0.0


def matches(text, patterns):
    text = str(text or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text) for t in patterns)


# These core tables must exist before ANY scanner, monitor or dashboard read.
# In particular, do not create a blank SQLite file through sqlite3.connect().
_REQUIRED_RADAR_TABLES = frozenset((
    "paper_v54", "paper_v542_b", "radar_v54", "v542_runs",
    "v541_config", "v542_decisions", "v57_monitor",
))


def _open_existing_database():
    path = Path(DB)
    if not path.is_file() or path.stat().st_size == 0:
        raise sqlite3.OperationalError(
            "Lokale SQLite-database ontbreekt of is leeg"
        )
    connection = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=30,
    )
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = _REQUIRED_RADAR_TABLES - tables
        if missing:
            raise sqlite3.OperationalError(
                "Ontbrekende radartabellen: " + ", ".join(sorted(missing))
            )
        return connection
    except BaseException:
        connection.close()
        raise


def db():
    """Open a verified existing DB; retry safe Supabase recovery ONCE.

    Applies to regular Streamlit runs AND independently refreshed fragments.
    Never manufacture an empty database on missing tables. Any recovery
    failure stays visible and blocks writes, preserving historical trades.
    """
    try:
        return _open_existing_database()
    except (sqlite3.Error, OSError) as first_error:
        with _LOCK:
            # Another fragment may have restored it while this one waited.
            try:
                return _open_existing_database()
            except (sqlite3.Error, OSError):
                pass
            try:
                backup_restore_at_start()
                # Existing backups are verified before restore; migration
                # is deliberately NOT re-run inside a fragment.
                return _open_existing_database()
            except (requests.RequestException, sqlite3.Error,
                    OSError, ValueError, RuntimeError) as restore_error:
                raise sqlite3.OperationalError(
                    "Radar-database niet beschikbaar (" + str(first_error)
                    + "). Veilig Supabase-herstel niet gelukt: "
                    + str(restore_error)
                ) from restore_error


def select(sql, args=()):
    with db() as c:
        return c.execute(sql, args).fetchall()


def initialize():
    # Existing V5.4 / V5.4.1 tables are reused, NEVER dropped or emptied.
    statements = [
        "CREATE TABLE IF NOT EXISTS discovered (address TEXT PRIMARY KEY,name TEXT,symbol TEXT,first_seen TEXT,last_seen TEXT)",
        "CREATE TABLE IF NOT EXISTS watchlist (address TEXT PRIMARY KEY,name TEXT,added_at TEXT)",
        "CREATE TABLE IF NOT EXISTS tracking_state (address TEXT PRIMARY KEY,first_seen TEXT,last_checked TEXT,last_found TEXT,missed_scans INTEGER DEFAULT 0,status TEXT)",
        """CREATE TABLE IF NOT EXISTS radar_v54 (address TEXT NOT NULL,scanned_at TEXT NOT NULL,name TEXT,symbol TEXT,pair_address TEXT,source TEXT,price REAL,liquidity REAL,volume_m5 REAL,buys_m5 INTEGER,sells_m5 INTEGER,age_hours REAL,narrative INTEGER,momentum INTEGER,evidence TEXT,phase TEXT,warnings TEXT,PRIMARY KEY(address,scanned_at))""",
        """CREATE TABLE IF NOT EXISTS paper_v54 (id INTEGER PRIMARY KEY AUTOINCREMENT,address TEXT NOT NULL,name TEXT,pair_address TEXT,opened_at TEXT NOT NULL,entry_market REAL NOT NULL,entry_effective REAL NOT NULL,amount_usd REAL NOT NULL,quantity REAL NOT NULL,fee_rate REAL NOT NULL,slippage_rate REAL NOT NULL,status TEXT DEFAULT 'OPEN',closed_at TEXT,exit_market REAL,exit_effective REAL,pnl_usd REAL,exit_reason TEXT,last_mark REAL,last_mark_at TEXT,peak_mark REAL)""",
        "CREATE TABLE IF NOT EXISTS v541_runs (scanned_at TEXT PRIMARY KEY,finished_at TEXT,fetched INTEGER,errors INTEGER,entries INTEGER,exits INTEGER,cause TEXT)",
        "CREATE TABLE IF NOT EXISTS v541_decisions (scan_at TEXT,address TEXT,name TEXT,outcome TEXT,reason TEXT,price REAL,liquidity REAL,PRIMARY KEY(scan_at,address))",
        "CREATE TABLE IF NOT EXISTS v541_config (name TEXT PRIMARY KEY,value TEXT)",
        """CREATE TABLE IF NOT EXISTS paper_v542_b (id INTEGER PRIMARY KEY AUTOINCREMENT,address TEXT NOT NULL,name TEXT,pair_address TEXT,opened_at TEXT NOT NULL,entry_market REAL NOT NULL,entry_effective REAL NOT NULL,amount_usd REAL NOT NULL,quantity REAL NOT NULL,fee_rate REAL NOT NULL,slippage_rate REAL NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',closed_at TEXT,exit_market REAL,exit_effective REAL,pnl_usd REAL,exit_reason TEXT,last_mark REAL,last_mark_at TEXT,peak_mark REAL)""",
        """CREATE TABLE IF NOT EXISTS v542_decisions (scan_at TEXT NOT NULL,address TEXT NOT NULL,name TEXT,strategy TEXT NOT NULL,outcome TEXT,reason TEXT,price REAL,liquidity REAL,PRIMARY KEY(scan_at,address,strategy))""",
        """CREATE TABLE IF NOT EXISTS v542_runs (started_at TEXT PRIMARY KEY,finished_at TEXT,cause TEXT,tokens INTEGER,api_errors INTEGER,a_entries INTEGER,b_entries INTEGER,a_exits INTEGER,b_exits INTEGER)""",
        "CREATE INDEX IF NOT EXISTS idx_v54_history ON radar_v54(address,scanned_at)",
        "CREATE INDEX IF NOT EXISTS idx_v54_paper_status ON paper_v54(status)",
        "CREATE INDEX IF NOT EXISTS idx_v542_paper_status ON paper_v542_b(status)",
        """CREATE TABLE IF NOT EXISTS v57_monitor (
            checked_at TEXT PRIMARY KEY, positions INTEGER, pairs INTEGER,
            exits INTEGER, api_errors INTEGER, backup_status TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_v57_monitor_time ON v57_monitor(checked_at)",
    ]
    # Old databases can contain a table without all new columns.
    # CREATE TABLE IF NOT EXISTS alone cannot migrate such tables.
    migrations = {
        "v542_runs": {
            "started_at": "TEXT", "finished_at": "TEXT", "cause": "TEXT",
            "tokens": "INTEGER", "api_errors": "INTEGER", "a_entries": "INTEGER",
            "b_entries": "INTEGER", "a_exits": "INTEGER", "b_exits": "INTEGER",
        },
        "v542_decisions": {
            "scan_at": "TEXT", "address": "TEXT", "name": "TEXT",
            "strategy": "TEXT", "outcome": "TEXT", "reason": "TEXT",
            "price": "REAL", "liquidity": "REAL",
        },
        "radar_v54": {
            "address": "TEXT", "scanned_at": "TEXT", "name": "TEXT",
            "symbol": "TEXT", "pair_address": "TEXT", "source": "TEXT",
            "price": "REAL", "liquidity": "REAL", "volume_m5": "REAL",
            "buys_m5": "INTEGER", "sells_m5": "INTEGER", "age_hours": "REAL",
            "narrative": "INTEGER", "momentum": "INTEGER", "evidence": "TEXT",
            "phase": "TEXT", "warnings": "TEXT",
        },
        "paper_v54": {
            "address": "TEXT", "name": "TEXT", "pair_address": "TEXT",
            "opened_at": "TEXT", "entry_market": "REAL", "entry_effective": "REAL",
            "amount_usd": "REAL", "quantity": "REAL", "fee_rate": "REAL",
            "slippage_rate": "REAL", "status": "TEXT", "closed_at": "TEXT",
            "exit_market": "REAL", "exit_effective": "REAL", "pnl_usd": "REAL",
            "exit_reason": "TEXT", "last_mark": "REAL", "last_mark_at": "TEXT",
            "peak_mark": "REAL",
        },
        "paper_v542_b": {
            "address": "TEXT", "name": "TEXT", "pair_address": "TEXT",
            "opened_at": "TEXT", "entry_market": "REAL", "entry_effective": "REAL",
            "amount_usd": "REAL", "quantity": "REAL", "fee_rate": "REAL",
            "slippage_rate": "REAL", "status": "TEXT", "closed_at": "TEXT",
            "exit_market": "REAL", "exit_effective": "REAL", "pnl_usd": "REAL",
            "exit_reason": "TEXT", "last_mark": "REAL", "last_mark_at": "TEXT",
            "peak_mark": "REAL",
        },
    }
    with db() as c:
        for statement in statements:
            if not statement.startswith("CREATE INDEX"):
                c.execute(statement)
        for table, columns in migrations.items():
            existing = {row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}
            for column, kind in columns.items():
                if column not in existing:
                    c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {kind}')
        # If old runs used scan_at, copy those timestamps to started_at.
        cols = {row[1] for row in c.execute('PRAGMA table_info("v542_runs")')}
        if "scan_at" in cols:
            c.execute("UPDATE v542_runs SET started_at=scan_at WHERE started_at IS NULL")
        for statement in statements:
            if statement.startswith("CREATE INDEX"):
                c.execute(statement)



def get_bool(key, default):
    rows = select("SELECT value FROM v541_config WHERE name=?", (key,))
    return rows[0][0] == "1" if rows else default


def set_bool(key, state):
    with db() as c:
        c.execute("INSERT INTO v541_config(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (key, "1" if state else "0"))


def api_get(path, params=None):
    response = HTTP.get(API + path, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def discover():
    addresses = {}
    errors = []

    def add(addr, source):
        if isinstance(addr, str) and ADDRESS.fullmatch(addr):
            addresses.setdefault(addr, set()).add(source)

    for term in SEARCHES:
        try:
            payload = api_get("/latest/dex/search", {"q": term})
            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                base = pair.get("baseToken") or {}
                if matches(f"{base.get('name', '')} {base.get('symbol', '')}", STRONG + RELATED):
                    add(base.get("address"), "DEX search")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Search {term}: {exc}")

    for path in ["/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"]:
        try:
            payload = api_get(path)
            for item in payload if isinstance(payload, list) else []:
                if item.get("chainId") != "solana":
                    continue
                links = " ".join(str(link.get("url", "")) for link in item.get("links") or [] if isinstance(link, dict))
                if matches(str(item.get("description") or "") + " " + links, STRONG + RELATED):
                    add(item.get("tokenAddress"), "DEX profile")
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Profile {path}: {exc}")

    lists = [
        "SELECT address FROM paper_v54 WHERE status='OPEN'",
        "SELECT address FROM paper_v542_b WHERE status='OPEN'",
        "SELECT address FROM watchlist ORDER BY added_at DESC",
        "SELECT address FROM tracking_state ORDER BY COALESCE(last_checked,first_seen) ASC LIMIT 200",
        "SELECT address FROM discovered ORDER BY last_seen ASC LIMIT 200",
    ]
    for sql in lists:
        for (address,) in select(sql):
            add(address, "Gevolgd")

    # Skip previously inactive tokens for 60 minutes unless they are in
    # a watchlist or have an open paper position. New tokens always pass.
    protected = {r[0] for r in select("SELECT address FROM watchlist")}
    protected |= {r[0] for r in select("SELECT address FROM paper_v54 WHERE status='OPEN'")}
    protected |= {r[0] for r in select("SELECT address FROM paper_v542_b WHERE status='OPEN'")}
    recent = select("""SELECT r.address,r.scanned_at,r.volume_m5 FROM radar_v54 r
        INNER JOIN (SELECT address,MAX(scanned_at) t FROM radar_v54 GROUP BY address) m
        ON m.address=r.address AND m.t=r.scanned_at""")
    inactive = {}
    for address, scanned, volume in recent:
        minutes = (datetime.now(timezone.utc)-parse_time(scanned)).total_seconds()/60
        if zero(volume) < 25 and minutes < 60 and address not in protected:
            inactive[address] = minutes
    filtered = {address: sources for address, sources in addresses.items()
                if address not in inactive}
    return filtered, errors, len(addresses)-len(filtered)


def market_pairs(addresses):
    results, fetched, errors = {}, set(), []
    for offset in range(0, len(addresses), 30):
        batch = addresses[offset:offset + 30]
        try:
            payload = api_get("/tokens/v1/solana/" + ",".join(batch))
            if not isinstance(payload, list):
                raise ValueError("Onverwacht marktdataformaat")
            fetched.update(batch)
            for pair in payload:
                if pair.get("chainId") != "solana":
                    continue
                address = (pair.get("baseToken") or {}).get("address")
                if address not in batch:
                    continue
                current = results.get(address)
                if current is None or zero((pair.get("liquidity") or {}).get("usd")) > zero((current.get("liquidity") or {}).get("usd")):
                    results[address] = pair
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Marktdata batch {offset // 30 + 1}: {exc}")
    return results, fetched, errors


def history(address, stamp):
    rows = select("SELECT scanned_at,price,volume_m5,pair_address FROM radar_v54 WHERE address=? AND scanned_at<? ORDER BY scanned_at DESC LIMIT 16", (address, stamp))
    return [(t, number(p), number(v), pair) for t, p, v, pair in rows]


def signals(address, stamp, price, volume, pair):
    if price is None or price <= 0:
        return 0, False, "Prijs ontbreekt of is ongeldig", "ONVOLLEDIGE_DATA"
    if volume is None:
        return 0, False, "5m-volume ontbreekt (niet hetzelfde als $0)", "ONVOLLEDIGE_DATA"
    if volume <= 0:
        return 0, False, "5m-volume is $0; geen actieve handel", "GEEN_HANDEL"
    previous = [x for x in history(address, stamp)
                if x[1] is not None and x[1] > 0
                and x[2] is not None and x[2] > 0 and x[3] == pair]
    if len(previous) < 2:
        return 0, False, f"{len(previous)+1}/3 metingen voor hetzelfde pair", "TE_WEINIG_METINGEN"
    recent = next((p for p in previous if 3.5 <=
                   (parse_time(stamp)-parse_time(p[0])).total_seconds()/60 <= 20), None)
    old = next((p for p in previous if recent and 3.5 <=
                (parse_time(recent[0])-parse_time(p[0])).total_seconds()/60 <= 20), None)
    if recent is None or old is None:
        return 0, False, "Geen drie metingen met telkens 3,5–20 min ertussen", "ONGELDIG_INTERVAL"
    gain = price / old[1] - 1
    gap_new = (parse_time(stamp)-parse_time(recent[0])).total_seconds()/60
    gap_old = (parse_time(recent[0])-parse_time(old[0])).total_seconds()/60
    p3 = old[1] < recent[1] < price
    v3 = old[2] < recent[2] < volume
    a_signal = p3 and gain >= .02 and v3 and volume >= 250
    b_signal = gain >= .05 and price > recent[1] and volume >= 250
    a_score = 80 if a_signal else 30 if (p3 or v3) else 0
    explanation = (
        f"Pair {pair[:9]}…; interval {gap_old:.1f}+{gap_new:.1f} min; "
        f"koers {gain:+.1%}; prijs stijgt 3x={p3}; "
        f"volume stijgt 3x={v3}; volume=${volume:,.0f}; "
        f"A voldaan={a_signal}; B +5%/laatste stijgend/volume>=250={b_signal}"
    )
    return a_score, b_signal, explanation, "GEMETEN"


def analyze(addr, pair, origins, stamp):
    base = pair.get("baseToken") or {}
    name = str(base.get("name") or "Onbekend")
    symbol = str(base.get("symbol") or "?")
    title = f"{name} {symbol}"
    narrative = 100 if matches(title, STRONG) else 65 if matches(title, RELATED) else 0
    created = number(pair.get("pairCreatedAt"))
    age = max(0.0, (parse_time(stamp).timestamp() * 1000 - created) / 3600000) if created else None
    price = number(pair.get("priceUsd"))
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = number((pair.get("volume") or {}).get("m5"))
    tx = (pair.get("txns") or {}).get("m5") or {}
    buys, sells = int(zero(tx.get("buys"))), int(zero(tx.get("sells")))
    pair_id = str(pair.get("pairAddress") or "")
    a_score, b_signal, evidence, diagnostic = signals(addr, stamp, price, volume, pair_id)
    transactions = buys + sells
    avg = volume / transactions if transactions and volume is not None else None
    micro = transactions >= 100 and avg is not None and avg < 3
    warnings = []
    if liquidity is None or liquidity < 10000:
        warnings.append("Liquiditeit onbekend of < $10.000")
    if micro:
        warnings.append("Microtransacties; mogelijke bots")
    warnings.append("Geen controle op mint/freeze, houders of verkoopbaarheid")
    return dict(address=addr, name=name, symbol=symbol, pair=pair_id, source=", ".join(sorted(origins)),
                narrative=narrative, age=age, price=price, liquidity=liquidity, volume=volume,
                buys=buys, sells=sells, micro=micro, avg=avg, a_score=a_score,
                b_signal=b_signal, evidence=evidence, diagnostic=diagnostic, warnings="; ".join(warnings))


def record_observations(records, stamp):
    with db() as c:
        for r in records:
            c.execute("INSERT INTO discovered(address,name,symbol,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET name=excluded.name,symbol=excluded.symbol,last_seen=excluded.last_seen", (r['address'], r['name'], r['symbol'], stamp, stamp))
            c.execute("INSERT INTO tracking_state(address,first_seen,last_checked,last_found,missed_scans,status) VALUES(?,?,?,?,0,'ACTIEF') ON CONFLICT(address) DO UPDATE SET last_checked=excluded.last_checked,last_found=excluded.last_found,missed_scans=0,status='ACTIEF'", (r['address'], stamp, stamp, stamp))
            phase = "MOMENTUM TER CONTROLE" if r['a_score'] == 80 else "PRE-TREND KANDIDAAT" if r['age'] is not None and r['age'] < 24 else "MONITOREN"
            c.execute("""INSERT OR REPLACE INTO radar_v54(address,scanned_at,name,symbol,pair_address,source,price,liquidity,volume_m5,buys_m5,sells_m5,age_hours,narrative,momentum,evidence,phase,warnings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (r['address'], stamp, r['name'], r['symbol'], r['pair'], r['source'], r['price'], r['liquidity'], r['volume'], r['buys'], r['sells'], r['age'], r['narrative'], r['a_score'], r['evidence'], phase, r['warnings']))


def decide(r, strategy, cfg, used):
    if r['address'] in used:
        return 'GEEN_INSTAP', 'Deze token werd in deze strategie al eens virtueel verhandeld'
    if r['narrative'] < 65:
        return 'GEEN_INSTAP', 'Geen duidelijke GTA-narrative'
    if r['diagnostic'] != 'GEMETEN':
        return 'GEEN_INSTAP', r['diagnostic'] + ': ' + r['evidence']
    if strategy == 'A' and r['a_score'] != 80:
        return 'GEEN_INSTAP', 'A faalt: eis 3 stijgende prijzen, minstens +2%, 3 stijgende volumes en $250: ' + r['evidence']
    if strategy == 'B' and not r['b_signal']:
        return 'GEEN_INSTAP', 'B faalt: eis +5% vanaf oudste prijs, laatste prijs stijgend en $250 volume: ' + r['evidence']
    if r['price'] is None or r['price'] <= 0:
        return 'GEBLOKKEERD', 'Prijs ongeldig'
    if r['liquidity'] is None or r['liquidity'] < cfg['min_liq']:
        return 'GEBLOKKEERD', 'Onvoldoende geverifieerde liquiditeit'
    if r['age'] is None or r['age'] > 24:
        return 'GEBLOKKEERD', 'Handelspaar ouder dan 24 uur of leeftijd onbekend'
    if r['volume'] is None or r['volume'] < 250:
        return 'GEBLOKKEERD', 'Onvoldoende 5m-volume'
    if r['buys'] <= r['sells']:
        return 'GEBLOKKEERD', 'Kooptransacties niet groter in aantal dan verkooptransacties'
    if strategy == 'A' and r['micro']:
        return 'GEBLOKKEERD', 'Gemiddelde transactie onder $3 bij minstens 100 transacties'
    if strategy == 'B' and r['micro']:
        return 'KANDIDAAT', 'EXPERIMENTEEL: microtransacties toegestaan; contractrisico blijft onbekend'
    return 'KANDIDAAT', 'Kwantitatieve voorwaarden gehaald; contractveiligheid niet gecontroleerd'


def paper_run(records, stamp, cfg, active_a, active_b):
    by_address = {r['address']: r for r in records}
    results = {'A': [0, 0], 'B': [0, 0]}
    ledgers = {'A': 'paper_v54', 'B': 'paper_v542_b'}
    for strategy, table in ledgers.items():
        enabled = active_a if strategy == 'A' else active_b
        with db() as c:
            open_trades = c.execute(f"SELECT id,address,opened_at,entry_market,quantity,amount_usd,fee_rate,slippage_rate,pair_address FROM {table} WHERE status='OPEN'").fetchall()
            used = {row[0] for row in c.execute(f"SELECT DISTINCT address FROM {table}")}
            for trade_id, addr, opened, entry, quantity, amount, fee, slip, original_pair in open_trades:
                r = by_address.get(addr)
                if not r or not r['price'] or r['price'] <= 0:
                    continue
                if original_pair and r['pair'] != original_pair:
                    c.execute(f"UPDATE {table} SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED' WHERE id=?", (trade_id,))
                    continue
                price = r['price']
                c.execute(f"UPDATE {table} SET last_mark=?,last_mark_at=?,peak_mark=MAX(COALESCE(peak_mark,0),?) WHERE id=?", (price, stamp, price, trade_id))
                change = price / entry - 1
                hours = (parse_time(stamp) - parse_time(opened)).total_seconds() / 3600
                reason = 'TAKE_PROFIT' if change >= cfg['take'] else 'STOP_LOSS' if change <= -cfg['stop'] else 'TIME_EXIT' if hours >= cfg['max_hours'] else None
                if not reason:
                    continue
                if r['liquidity'] is None or r['liquidity'] < cfg['min_liq']:
                    c.execute(f"UPDATE {table} SET exit_reason='EXIT_BLOCKED_LIQUIDITY' WHERE id=?", (trade_id,))
                    continue
                effective_exit = price * (1 - slip)
                pnl = quantity * effective_exit * (1 - fee) - amount
                c.execute(f"UPDATE {table} SET status='CLOSED',closed_at=?,exit_market=?,exit_effective=?,pnl_usd=?,exit_reason=? WHERE id=? AND status='OPEN'", (stamp, price, effective_exit, pnl, reason, trade_id))
                results[strategy][1] += 1

            current_open = c.execute(f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'").fetchone()[0]
            for r in records:
                outcome, reason = decide(r, strategy, cfg, used)
                if outcome == 'KANDIDAAT' and not enabled:
                    outcome, reason = 'UITGESCHAKELD', f'Strategie {strategy} paper trading staat uit; ' + reason
                elif outcome == 'KANDIDAAT' and current_open >= cfg['max_positions']:
                    outcome, reason = 'GEBLOKKEERD', f'Maximaal {cfg["max_positions"]} gelijktijdige posities bereikt'
                elif outcome == 'KANDIDAAT':
                    entry_effective = r['price'] * (1 + cfg['slip'])
                    qty = cfg['amount'] * (1 - cfg['fee']) / entry_effective
                    c.execute(f"""INSERT INTO {table}(address,name,pair_address,opened_at,entry_market,entry_effective,amount_usd,quantity,fee_rate,slippage_rate,status,last_mark,last_mark_at,peak_mark) VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?)""", (r['address'], r['name'], r['pair'], stamp, r['price'], entry_effective, cfg['amount'], qty, cfg['fee'], cfg['slip'], r['price'], stamp, r['price']))
                    used.add(r['address'])
                    current_open += 1
                    results[strategy][0] += 1
                    outcome, reason = 'PAPER_ENTRY', 'Virtuele order geregistreerd; geen controle op daadwerkelijke uitvoerbaarheid'
                c.execute("INSERT OR REPLACE INTO v542_decisions(scan_at,address,name,strategy,outcome,reason,price,liquidity) VALUES(?,?,?,?,?,?,?,?)", (stamp, r['address'], r['name'], strategy, outcome, reason, r['price'], r['liquidity']))
    return results


def scan(trigger, cfg, active_a, active_b):
    with _LOCK:
        return _scan_locked(trigger, cfg, active_a, active_b)


def _scan_locked(trigger, cfg, active_a, active_b):
    start = utcnow()
    addresses, errors, cooled = discover()
    pairs, fetched, fetch_errors = market_pairs(list(addresses))
    errors.extend(fetch_errors)
    observed = []
    open_addresses = {row[0] for row in select("SELECT address FROM paper_v54 WHERE status='OPEN'")}
    open_addresses |= {row[0] for row in select("SELECT address FROM paper_v542_b WHERE status='OPEN'")}
    for addr, pair in pairs.items():
        try:
            r = analyze(addr, pair, addresses[addr], start)
            if r['narrative'] >= 65 or addr in open_addresses:
                observed.append(r)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"Analyse {addr[:10]}: {exc}")
    # An API error must never be interpreted as a missing/delisted token.
    record_observations(observed, start)
    results = paper_run(observed, start, cfg, active_a, active_b)
    end = utcnow()
    with db() as c:
        c.execute("""INSERT INTO v542_runs(started_at,finished_at,cause,tokens,api_errors,a_entries,b_entries,a_exits,b_exits)
                     VALUES(?,?,?,?,?,?,?,?,?)""", (start, end, trigger, len(observed), len(errors), results['A'][0], results['B'][0], results['A'][1], results['B'][1]))
    try:
        backup_remote(force=True)
    except (requests.RequestException, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        errors.append("Externe back-up mislukt: " + str(exc))
    return len(observed), results, errors, cooled


def monitor_positions(cfg):
    # Executes only on open paper positions, independently of discovery.
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        tables = [('A', 'paper_v54'), ('B', 'paper_v542_b')]
        opens = []
        for strategy, table in tables:
            for trade in select(f"""SELECT id,address,pair_address,opened_at,
                    entry_market,quantity,amount_usd,fee_rate,slippage_rate
                    FROM {table} WHERE status='OPEN'"""):
                opens.append((strategy, table, trade))
        if not opens:
            return {'open': 0, 'checked': 0, 'exits': 0, 'errors': []}
        addresses = sorted({item[2][1] for item in opens})
        pairs, fetched, errors = market_pairs(addresses)
        stamp = utcnow()
        exits = 0
        with db() as c:
            for strategy, table, trade in opens:
                tid, addr, old_pair, opened, entry, qty, amount, fee, slip = trade
                pair = pairs.get(addr)
                if pair is None:
                    continue  # No API data is not proof that the price is zero.
                pid = str(pair.get('pairAddress') or '')
                if old_pair and old_pair != pid:
                    c.execute(f"UPDATE {table} SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED' WHERE id=? AND status='OPEN'", (tid,))
                    continue
                price = number(pair.get('priceUsd'))
                liq = number((pair.get('liquidity') or {}).get('usd'))
                if price is None or price <= 0:
                    continue
                c.execute(f"""UPDATE {table} SET last_mark=?,last_mark_at=?,
                    peak_mark=MAX(COALESCE(peak_mark,0),?)
                    WHERE id=? AND status='OPEN'""", (price, stamp, price, tid))
                change = price / entry - 1
                held = (parse_time(stamp)-parse_time(opened)).total_seconds()/3600
                reason = ('TAKE_PROFIT' if change >= cfg['take'] else
                          'STOP_LOSS' if change <= -cfg['stop'] else
                          'TIME_EXIT' if held >= cfg['max_hours'] else None)
                if reason is None:
                    continue
                if liq is None or liq < cfg['min_liq']:
                    c.execute(f"UPDATE {table} SET exit_reason='EXIT_BLOCKED_LIQUIDITY' WHERE id=? AND status='OPEN'", (tid,))
                    continue
                effective = price*(1-zero(slip))
                pnl = qty*effective*(1-zero(fee))-amount
                cursor = c.execute(f"""UPDATE {table} SET status='CLOSED',
                    closed_at=?,exit_market=?,exit_effective=?,pnl_usd=?,exit_reason=?
                    WHERE id=? AND status='OPEN'""",
                    (stamp, price, effective, pnl, reason, tid))
                exits += cursor.rowcount
            c.execute("""INSERT OR REPLACE INTO v57_monitor
                (checked_at,positions,pairs,exits,api_errors,backup_status)
                VALUES(?,?,?,?,?,?)""", (stamp,len(opens),len(pairs),exits,len(errors),'PENDING'))
        try:
            status = backup_remote(force=exits > 0)
        except (requests.RequestException, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            status = "BACKUP ERROR: " + str(exc)
            errors.append(status)
        with db() as c:
            c.execute("UPDATE v57_monitor SET backup_status=? WHERE checked_at=?", (status,stamp))
        return {'open':len(opens),'checked':len(pairs),'exits':exits,'errors':errors,'backup':status}
    finally:
        _LOCK.release()


def latest_tokens():
    row = select("SELECT started_at FROM v542_runs ORDER BY started_at DESC LIMIT 1")
    if not row:
        return pd.DataFrame()
    data = select("SELECT name,symbol,address,pair_address,age_hours,price,liquidity,volume_m5,buys_m5,sells_m5,momentum,evidence,warnings FROM radar_v54 WHERE scanned_at=?", (row[0][0],))
    return pd.DataFrame(data, columns=['Naam', 'Symbool', 'Adres', 'Pair', 'Leeftijd uur', 'Prijs $', 'Liquiditeit $', '5m-volume $', 'Buys', 'Sells', 'A momentum', 'Trend', 'Waarschuwingen'])


def ledger_view(table):
    rows = select(f"SELECT id,name,address,opened_at,amount_usd,entry_market,last_mark,status,pnl_usd,exit_reason FROM {table} ORDER BY id DESC LIMIT 300")
    data = pd.DataFrame(rows, columns=['ID', 'Naam', 'Adres', 'Open UTC', 'Inzet $', 'Instap $', 'Laatste prijs $', 'Status', 'Gerealiseerd P/L $', 'Exitreden'])
    if not data.empty:
        data['Geopend België'] = data['Open UTC'].apply(be_time)
        data = data.drop(columns=['Open UTC'])
    return data


def paper_risk_frame(table):
    """Illustrative net liquidation mark; not an executable swap quote."""
    rows = select(f"""SELECT id,name,address,status,amount_usd,quantity,
           fee_rate,slippage_rate,entry_market,opened_at,pair_address,
           last_mark,last_mark_at,exit_reason FROM {table}
           WHERE status='OPEN' ORDER BY id DESC""")
    result = []
    for (id_, name, addr, status, stake, qty, fee, slip, entry,
         opened, original_pair, mark, mark_at, reason) in rows:
        live = select("""SELECT scanned_at,price,liquidity,pair_address
                         FROM radar_v54 WHERE address=?
                         ORDER BY scanned_at DESC LIMIT 1""", (addr,))
        current = live[0] if live else None
        pair_changed = bool(current and original_pair and current[3] != original_pair)
        price = (current[1] if current else None) if not pair_changed else None
        liq = current[2] if current else None
        sale = (qty * price * (1-zero(slip)) * (1-zero(fee))) if price and qty else None
        pnl = sale-stake if sale is not None and stake is not None else None
        net_pct = 100*pnl/stake if pnl is not None and stake else None
        age_min = ((datetime.now(timezone.utc)-parse_time(current[0])).total_seconds()/60
                   if current else None)
        alert = (
            'PAIR WISSEL — NIET VERGELIJKEN' if pair_changed else
            'GEEN ACTUELE PRIJS' if price is None else
            'DATA OUDER DAN 20 MIN' if age_min is not None and age_min > 20 else
            'LIQUIDITEIT < $10K' if liq is None or liq < 10000 else
            'NETTO <= -15%' if net_pct is not None and net_pct <= -15 else
            'NETTO <= -10%' if net_pct is not None and net_pct <= -10 else
            'Onbevestigde verkoopbaarheid'
        )
        result.append(dict(ID=id_, Naam=name, Adres=addr, Marktprijs=price,
                           Liquiditeit=liq, **{'Netto mark P/L %':net_pct},
                           **{'Netto mark P/L $':pnl},
                           **{'Data leeftijd (min)':age_min},
                           Waarschuwing=alert, Exit_status=reason or 'OPEN'))
    return pd.DataFrame(result)


def learning_panel():
    """Read-only inspection of collector data, including manual uploads."""
    st.subheader('🧠 Learning Engine — status, nog géén AI-model')
    st.caption('GitHub Actions en Streamlit hebben verschillende bestandssystemen. '
               'Upload de GitHub-bestanden hieronder om ze hier te analyseren.')
    uploaded_json = st.file_uploader('Optioneel: data/latest_scan.json', type=['json'], key='learning_json')
    if uploaded_json is not None:
        try:
            payload = json.load(uploaded_json)
            if not isinstance(payload, dict):
                raise ValueError('JSON-object verwacht')
            cols = st.columns(4)
            for col, key, label in zip(cols,
                        ['discovered','recorded','total_snapshots','total_labels'],
                        ['Ontdekt','Deze run','Totaal snapshots','Leerlabels']):
                col.metric(label, str(payload.get(key, '-')))
            st.write('Laatste collector-run (UTC):', payload.get('finished_at_utc', '-'))
            errors = payload.get('errors', [])
            if errors:
                st.warning(f"Collector meldde {len(errors)} fout(en)")
                st.write(errors[:10])
        except (ValueError, json.JSONDecodeError, UnicodeError) as exc:
            st.error(f'Ongeldig statusbestand: {exc}')

    uploaded_db = st.file_uploader('Optioneel: data/learning.db', type=['db'], key='learning_db')
    if uploaded_db is None:
        st.info('Geen Learning-DB geüpload. Er wordt niets automatisch uit GitHub gesynchroniseerd.')
        return
    try:
        if uploaded_db.size > 100_000_000:
            st.error('Database te groot voor deze upload (max. 100MB).')
            return
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            tmp.write(uploaded_db.getvalue())
            tmp.flush()
            # Open uploaded SQLite read-only. No schema updates or writes.
            uri = 'file:' + tmp.name + '?mode=ro&immutable=1'
            with sqlite3.connect(uri, uri=True) as c:
                total = c.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
                labels = c.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0]
                c1,c2 = st.columns(2)
                c1.metric('Historische snapshots', total)
                c2.metric('Uitkomsten 15/30/60m', labels)
                if labels:
                    df = pd.read_sql_query("""SELECT horizon_minutes, COUNT(*) n,
                        AVG(return_pct) avg_pct, AVG(crash_30pct) crash_share
                        FROM outcomes GROUP BY horizon_minutes
                        ORDER BY horizon_minutes""", c)
                    df['crash_share'] = df['crash_share']*100
                    df.columns = ['Minuten','Aantal labels','Gem. koersverandering %',
                                  'Crashes <= -30% (%)']
                    st.dataframe(df, hide_index=True, width='stretch')
                    st.caption('Indicatieve koerslabels, geen uitvoerbare trades. '
                               'Snapshots zijn gecorreleerd; geen bewezen voorspelkracht.')
                latest = pd.read_sql_query("""SELECT name,token_address,pair_address,
                    scanned_at,price_usd,liquidity_usd,volume_m5
                    FROM snapshots ORDER BY scanned_at DESC LIMIT 150""", c)
                st.dataframe(latest, hide_index=True, width='stretch')
    except (sqlite3.Error, ValueError) as exc:
        st.error(f'Learning-DB kon niet worden gelezen: {exc}')



try:
    initialize()
except (sqlite3.Error, OSError, RuntimeError) as exc:
    st.error("Lokale database-initialisatie mislukt: " + str(exc))
    st.stop()
st.title('🎮 GTA 6 Radar V5.8.1')
st.caption('A/B-paperstrategieën · 15s papercontrole · actieve tokens eerst · bestaande SQLite-tabellen behouden')
st.warning('Uitsluitend gesimuleerde transacties. Een koers of stoploss uit marktdata betekent niet dat een echte koop/verkoop uitvoerbaar zou zijn. Mint/freeze, walletconcentratie en honeypots worden niet gecontroleerd.')

if 'v542_auto' not in st.session_state:
    st.session_state.v542_auto = get_bool('auto_scan', True)
if 'v542_a' not in st.session_state:
    st.session_state.v542_a = get_bool('paper', False)
if 'v542_b' not in st.session_state:
    st.session_state.v542_b = get_bool('paper_b', False)


def change_auto():
    set_bool('auto_scan', st.session_state.v542_auto)


def change_a():
    set_bool('paper', st.session_state.v542_a)


def change_b():
    set_bool('paper_b', st.session_state.v542_b)


if remote_settings():
    st.sidebar.success('Supabase-configuratie aanwezig (upload apart controleren)')
else:
    st.sidebar.warning('Geen externe back-up: SQLite op Streamlit Cloud is niet permanent!')
st.sidebar.caption(BACKUP_BOOT)
recent_backup = select("SELECT checked_at,backup_status FROM v57_monitor WHERE backup_status LIKE 'BACKUP OK%' ORDER BY checked_at DESC LIMIT 1")
if recent_backup:
    st.sidebar.caption('Laatste geslaagde monitorback-up: ' + be_time(recent_backup[0][0]))
else:
    st.sidebar.caption('Nog geen geslaagde monitorback-up geregistreerd in deze database')

st.sidebar.header('⚙️ Scanner')
st.sidebar.toggle('Automatisch scannen (actieve sessie)', key='v542_auto', on_change=change_auto)
st.sidebar.toggle('Strategie A: paperinstappen', key='v542_a', on_change=change_a)
st.sidebar.toggle('Strategie B: paperinstappen', key='v542_b', on_change=change_b)
interval = st.sidebar.selectbox('Scaninterval (min)', [5, 10, 15], index=0)
st.sidebar.caption('A = stijgende prijs én 5m-volume + microtransactiefilter. B = ≥5% prijsstijging over drie meetpunten, recent stijgende prijs, minimumvolume; microtransacties worden gelogd maar niet automatisch geblokkeerd.')
minimum_liq = st.sidebar.number_input('Minimale liquiditeit ($)', min_value=1000.0, max_value=1000000.0, value=10000.0, step=1000.0)
amount = st.sidebar.number_input('Virtuele inzet per positie ($)', min_value=5.0, max_value=1000.0, value=25.0, step=5.0)
max_positions = st.sidebar.number_input('Max. open posities per strategie', min_value=1, max_value=20, value=3)
fee_pct = st.sidebar.number_input('Fee per zijde (%)', 0.0, 10.0, 1.0, 0.25)
slip_pct = st.sidebar.number_input('Slippage per zijde (%)', 0.0, 30.0, 2.0, 0.5)
take_pct = st.sidebar.number_input('Take profit (%)', 5, 500, 30, 5)
stop_pct = st.sidebar.number_input('Stop loss (%)', 5, 95, 15, 5)
max_hours = st.sidebar.number_input('Max. looptijd (uur)', 1, 72, 4)
settings = dict(min_liq=minimum_liq, amount=amount, max_positions=max_positions,
                fee=fee_pct / 100, slip=slip_pct / 100, take=take_pct / 100,
                stop=stop_pct / 100, max_hours=max_hours)


def momentum_watchlist():
    """Read-only research shortlist; no changes to paper-trading decisions."""
    latest = select("SELECT started_at FROM v542_runs ORDER BY started_at DESC LIMIT 1")
    if not latest:
        return pd.DataFrame()
    scanned_at = latest[0][0]
    candidates = select("""SELECT address, name, pair_address, price,
        liquidity, volume_m5, buys_m5, sells_m5, age_hours
        FROM radar_v54 WHERE scanned_at=?""", (scanned_at,))
    result = []
    for address, name, pair, price, liq, vol, buys, sells, age in candidates:
        if not pair or not price or price <= 0:
            continue
        series = select("""SELECT scanned_at,price,volume_m5 FROM radar_v54
            WHERE address=? AND pair_address=? AND scanned_at<=?
            ORDER BY scanned_at DESC LIMIT 12""", (address, pair, scanned_at))
        current = next((p for p in series if p[0] == scanned_at), None)
        if current is None:
            continue
        previous = [p for p in series if p[0] != scanned_at
                    and p[1] is not None and p[1] > 0
                    and p[2] is not None and p[2] > 0]
        recent = next((p for p in previous if 3.5 <=
                       (parse_time(scanned_at)-parse_time(p[0])).total_seconds()/60 <= 20), None)
        old = next((p for p in previous if recent and 3.5 <=
                    (parse_time(recent[0])-parse_time(p[0])).total_seconds()/60 <= 20), None)
        if not recent or not old or vol is None or vol <= 0:
            continue
        gain = price / old[1] - 1
        rising_prices = old[1] < recent[1] < price
        rising_volumes = old[2] < recent[2] < vol
        a_signal = rising_prices and gain >= .02 and rising_volumes and vol >= 250
        b_signal = gain >= .05 and price > recent[1] and vol >= 250
        liq_ok = liq is not None and liq >= 10000
        activity_ok = vol >= 250
        if (a_signal or b_signal) and liq_ok:
            category = 'Momentumkandidaat'
            note = 'Kwantitatief A/B-signaal; overige filters en contractrisico nog te toetsen'
            rank = 0
        elif (rising_prices and gain >= .02 and vol >= 100 and liq_ok
              or gain >= .04 and price > recent[1] and vol >= 100 and liq_ok):
            category = 'Bijna instapklaar'
            note = ('Volume stijgt niet 3×' if not rising_volumes else
                    '5m-volume onder $250' if not activity_ok else
                    'Niet alle A/B-voorwaarden voldaan')
            rank = 1
        else:
            category = 'Afgewezen'
            note = 'Prijs/volume-momentum onvoldoende voor shortlist'
            rank = 2
        result.append({
            'Categorie': category, 'Token': name, 'Adres': address,
            'Koers 3 metingen %': round(gain*100, 2),
            '5m-volume $': round(vol, 2),
            'Liquiditeit $': round(liq, 2) if liq is not None else None,
            'Prijzen stijgen 3×': rising_prices,
            'Volumes stijgen 3×': rising_volumes,
            'Waarom': note, '_rank': rank,
        })
    if not result:
        return pd.DataFrame()
    return pd.DataFrame(result).sort_values(
        ['_rank', '5m-volume $'], ascending=[True, False]
    ).drop(columns=['_rank'])


@st.fragment(run_every='60s')
def dashboard():
    manual = st.button('🔄 Scan nu', type='primary')
    try:
        last = select('SELECT started_at,finished_at,cause FROM v542_runs ORDER BY started_at DESC LIMIT 1')
    except (sqlite3.Error, OSError) as exc:
        st.error("Lokale scannerdatabase niet beschikbaar: " + str(exc))
        st.info("Herstart de app om de gecontroleerde Supabase-herstelprocedure te gebruiken.")
        st.stop()
    last_end = parse_time(last[0][1]) if last and last[0][1] else None
    current = datetime.now(timezone.utc)
    next_scan = last_end + timedelta(minutes=interval) if last_end else current
    st.subheader('🕒 Scanplanning — Belgische tijd')
    st.write('Automatisch scannen:', '**AAN**' if st.session_state.v542_auto else '**UIT**')
    st.write('Laatste scan:', f"**{be_time(last[0][1])}** ({last[0][2]})" if last else 'Nog geen scan in V5.4.3')
    st.write('Volgende scan vanaf:', f'**{be_time(next_scan.isoformat())}**' if st.session_state.v542_auto else 'Niet ingepland')
    st.caption('De pagina controleert ongeveer elke minuut of een scan verschuldigd is. Een slapende tab/server of lange API-call kan het interval vertragen. Geen gegarandeerde 24/7-werking.')
    due = st.session_state.v542_auto and current >= next_scan
    if manual or due:
        try:
            with st.spinner('Solana-tokens scannen en beide strategieën evalueren...'):
                count, result, errors, cooled = scan('MANUEEL' if manual else 'AUTOMATISCH', settings, st.session_state.v542_a, st.session_state.v542_b)
            st.success(f"Scan klaar: {count} tokens · {cooled} inactieve tokens tijdelijk overgeslagen · A: {result['A'][0]} instappen · B: {result['B'][0]} instappen")
            if errors:
                with st.expander(f'{len(errors)} meldingen over API/data'):
                    st.write(errors[:35])
        except (sqlite3.Error, requests.RequestException, ValueError, TypeError) as exc:
            st.error(f'Scanfout: {exc}')

    tokens = latest_tokens()
    a_open = select("SELECT COUNT(*) FROM paper_v54 WHERE status='OPEN'")[0][0]
    b_open = select("SELECT COUNT(*) FROM paper_v542_b WHERE status='OPEN'")[0][0]
    a, b, c, d = st.columns(4)
    a.metric('Tokens laatste scan', len(tokens))
    b.metric('A: momentum 80', int((tokens['A momentum'] == 80).sum()) if not tokens.empty else 0)
    c.metric('A: open posities', a_open)
    d.metric('B: open posities', b_open)

    st.subheader('📡 Laatste tokenmeting')
    if tokens.empty:
        st.info('Nog geen V5.4.3-scan opgeslagen. Gebruik Scan nu of houd de pagina actief voor de automatische scan.')
    else:
        st.dataframe(tokens.sort_values(['5m-volume $', 'A momentum'], ascending=False, na_position='last'), hide_index=True, width='stretch')
        st.download_button('📥 Tokenmetingen CSV', tokens.to_csv(index=False).encode('utf-8'), 'gta6_v542_tokens.csv', 'text/csv')
        choices = {f"{r['Naam']} · {r['Adres'][:12]}": r['Adres'] for _, r in tokens.iterrows()}
        chosen = st.selectbox('Tokenhistoriek', list(choices))
        address = choices[chosen]
        one = tokens[tokens['Adres'] == address].iloc[0]
        st.code(address, language='text')
        st.link_button('Bekijk DexScreener', 'https://dexscreener.com/solana/' + str(one['Pair'] or address))
        if st.button('⭐ Voeg toe aan watchlist'):
            with db() as c:
                c.execute('INSERT OR IGNORE INTO watchlist(address,name,added_at) VALUES(?,?,?)', (address, one['Naam'], utcnow()))
            st.success('Toegevoegd')
        hist = select('SELECT scanned_at,price,volume_m5 FROM radar_v54 WHERE address=? ORDER BY scanned_at DESC LIMIT 120', (address,))
        if len(hist) >= 2:
            hd = pd.DataFrame(hist, columns=['Tijd', 'Prijs $', '5m-volume $'])
            hd['Tijd'] = pd.to_datetime(hd['Tijd'], utc=True).dt.tz_convert('Europe/Brussels')
            hd = hd.sort_values('Tijd').set_index('Tijd')
            st.line_chart(hd[['Prijs $']])
            st.line_chart(hd[['5m-volume $']])

    st.subheader('🔎 Vergelijking beslissingen A en B')
    newest = select('SELECT started_at FROM v542_runs ORDER BY started_at DESC LIMIT 1')
    if newest:
        decision_rows = select('SELECT name,address,strategy,outcome,reason,price,liquidity FROM v542_decisions WHERE scan_at=? ORDER BY strategy,outcome,name', (newest[0][0],))
        decisions = pd.DataFrame(decision_rows, columns=['Naam', 'Adres', 'Strategie', 'Uitkomst', 'Reden', 'Prijs $', 'Liquiditeit $'])
        filter_strategy = st.radio('Toon strategie', ['Allebei', 'A', 'B'], horizontal=True)
        view = decisions if filter_strategy == 'Allebei' else decisions[decisions['Strategie'] == filter_strategy]
        st.dataframe(view, hide_index=True, width='stretch')
        st.download_button('📥 A/B-beslissingen CSV', decisions.to_csv(index=False).encode('utf-8'), 'gta6_v542_ab_beslissingen.csv', 'text/csv')

    st.subheader('🎯 Momentum-watchlist — alleen onderzoek')
    st.caption('Gebaseerd op de nieuwste Streamlit-scans en drie metingen van hetzelfde pair. '
               'Geen orders, geen nieuwe paperinstappen en geen versoepelde risicofilters. '
               'De GitHub A/B/C-engine gebruikt een afzonderlijke database.')
    shortlist = momentum_watchlist()
    if shortlist.empty:
        st.info('Nog geen kandidaten met drie recente, geldige metingen en handelsvolume.')
    else:
        totals = shortlist['Categorie'].value_counts()
        left, mid, right = st.columns(3)
        left.metric('Momentumkandidaten', int(totals.get('Momentumkandidaat', 0)))
        mid.metric('Bijna instapklaar', int(totals.get('Bijna instapklaar', 0)))
        right.metric('Afgewezen', int(totals.get('Afgewezen', 0)))
        chosen_status = st.selectbox('Watchlist filter',
             ['Interessant', 'Alle', 'Momentumkandidaat', 'Bijna instapklaar', 'Afgewezen'],
             key='momentum_watchlist_filter')
        if chosen_status == 'Interessant':
            visible = shortlist[shortlist['Categorie'] != 'Afgewezen']
        elif chosen_status == 'Alle':
            visible = shortlist
        else:
            visible = shortlist[shortlist['Categorie'] == chosen_status]
        if visible.empty:
            st.info('Momenteel geen tokens in deze categorie.')
        else:
            st.dataframe(visible, hide_index=True, width='stretch')
            st.download_button('📥 Momentum-watchlist CSV',
                visible.to_csv(index=False).encode('utf-8'),
                'gta6_momentum_watchlist.csv', 'text/csv',
                key='momentum_watchlist_csv')

    st.subheader('🧪 Paperportfolio’s (volledig gescheiden)')
    ta, tb = st.tabs(['Strategie A — streng', 'Strategie B — experimenteel'])
    for tab, table in [(ta, 'paper_v54'), (tb, 'paper_v542_b')]:
        with tab:
            trades = ledger_view(table)
            if trades.empty:
                st.info('Nog geen virtuele posities in deze strategie.')
            else:
                closed = trades[trades['Status'] == 'CLOSED']
                cols = st.columns(3)
                cols[0].metric('Totaal geregistreerde posities', len(trades))
                cols[1].metric('Gerealiseerd P/L', f"${closed['Gerealiseerd P/L $'].sum():,.2f}")
                cols[2].metric('Winrate gesloten', f"{(closed['Gerealiseerd P/L $'] > 0).mean() * 100:.1f}%" if not closed.empty else '-')
                st.dataframe(trades, hide_index=True, width='stretch')
                st.download_button(f"📥 Trades {table} CSV", trades.to_csv(index=False).encode('utf-8'), f'{table}.csv', 'text/csv')

    st.subheader('🚨 Risicobewaking — open paperposities')
    st.caption('Deze bedragen zijn berekend uit laatst gevonden marktprijzen, niet uit verkoopquotes. '
               'De simulator kan niet garanderen dat een verlies stopt bij 15%.')
    for name, table in [('Strategie A','paper_v54'), ('Strategie B','paper_v542_b')]:
        risk = paper_risk_frame(table)
        if not risk.empty:
            st.write('**' + name + '**')
            st.dataframe(risk, hide_index=True, width='stretch')
    with st.expander('🧠 Learning-dataset onderzoeken (GitHub-export)'):
        learning_panel()

    st.subheader('🕒 Scanlog (Belgische tijd)')
    logs = select('SELECT started_at,finished_at,cause,tokens,api_errors,a_entries,b_entries,a_exits,b_exits FROM v542_runs ORDER BY started_at DESC LIMIT 40')
    if logs:
        ld = pd.DataFrame(logs, columns=['Begin UTC', 'Einde UTC', 'Trigger', 'Tokens', 'API-meldingen', 'A instappen', 'B instappen', 'A exits', 'B exits'])
        ld['Begin België'] = ld['Begin UTC'].apply(be_time)
        ld['Einde België'] = ld['Einde UTC'].apply(be_time)
        st.dataframe(ld.drop(columns=['Begin UTC', 'Einde UTC']), hide_index=True, width='stretch')


@st.fragment(run_every='15s')
def paper_monitor_fragment():
    st.subheader('⚡ Papertrade-monitor — 15 seconden')
    st.caption('Werkt alleen zolang de Streamlit-sessie actief blijft. Een nieuwe koers is geen echte swapquote.')
    try:
        result = monitor_positions(settings)
    except (sqlite3.Error, OSError) as exc:
        st.error("Lokale paperdatabase niet beschikbaar: " + str(exc))
        st.info("Geen data overschreven. Herstart de app zodat veilig herstel via Supabase kan lopen.")
        st.stop()
    if result is None:
        st.info('Hoofdscan actief; papercontrole wordt daarna hervat.')
    elif result['open'] == 0:
        st.info('Geen open papertrades; geen extra API-verzoek nodig.')
    else:
        x, y, z = st.columns(3)
        x.metric('Open bij controle', result['open'])
        y.metric('Tokens met prijsdata', result['checked'])
        z.metric('Virtuele exits', result['exits'])
        if result['errors']:
            st.warning('Monitor/backup: ' + ' | '.join(result['errors'][:3]))
    rows = select('SELECT checked_at,positions,pairs,exits,backup_status FROM v57_monitor ORDER BY checked_at DESC LIMIT 5')
    if rows:
        st.dataframe(pd.DataFrame(rows,columns=['UTC','Posities','Pairs','Exits','Back-up']),hide_index=True,width='stretch')

paper_monitor_fragment()
dashboard()
st.caption('V5.7.1 — Simulatie gebruikt gesamplede marktprijzen, schattingen voor fees/slippage en veronderstelde fills; resultaten zijn geen bewezen rendement of gegarandeerde verkoopbaarheid. SQLite op tijdelijke hosting kan verloren gaan zonder persistent volume/back-up.')


# ============================================================
# RECOVERY V2 — veilige samenvoeging met expliciete bevestiging
# ============================================================

def _recovery_trade_key(trade):
    return (trade["address"], trade["opened_at"])


def _recovery_source_data():
    cfg = _storage_config()
    if cfg is None:
        raise RuntimeError("Supabase Secrets ontbreken")
    object_name = "radar/gta6_radar_hersteld_v2.db"
    blob = _get_object(cfg, object_name)
    if blob is None:
        raise RuntimeError("Herstelkopie niet gevonden: " + object_name)
    stats = _inspect_database(blob)
    if stats["paper_v542_b"] != 4:
        raise RuntimeError("Verkeerde herstelbron: verwacht exact 4 B-trades")
    return cfg, blob, stats


def _recovery_read_source(blob):
    with tempfile.NamedTemporaryFile(suffix=".db") as temp:
        temp.write(blob)
        temp.flush()
        with sqlite3.connect("file:" + temp.name + "?mode=ro", uri=True) as src:
            src.row_factory = sqlite3.Row
            trades = [
                dict(row) for row in src.execute(
                    "SELECT * FROM paper_v542_b ORDER BY id"
                )
            ]
            historical = {}
            for table in ("radar_v54", "v542_runs", "v542_decisions", "v57_monitor"):
                historical[table] = [
                    dict(row) for row in src.execute("SELECT * FROM " + table)
                ]
    if len(trades) != 4 or any(
        trade["status"] != "CLOSED" or trade["pnl_usd"] is None
        for trade in trades
    ):
        raise RuntimeError("Herstelbron heeft geen 4 volledig afgesloten trades")
    if len({_recovery_trade_key(t) for t in trades}) != 4:
        raise RuntimeError("Herstelbron heeft dubbele instapidentiteiten")
    return trades, historical


def _recovery_preview(trades):
    with db() as conn:
        conn.row_factory = sqlite3.Row
        current = {}
        for row in conn.execute(
            "SELECT id,address,opened_at,status,pnl_usd FROM paper_v542_b"
        ):
            key = (row["address"], row["opened_at"])
            if key in current:
                raise RuntimeError("Dubbele lokale trade-identiteit: herstel geblokkeerd")
            current[key] = dict(row)
    missing, open_trades, conflicts = 0, 0, []
    for trade in trades:
        existing = current.get(_recovery_trade_key(trade))
        if existing is None:
            missing += 1
        elif existing["status"] == "OPEN":
            open_trades += 1
        elif (
            existing["status"] != "CLOSED"
            or existing["pnl_usd"] is None
            or abs(float(existing["pnl_usd"]) - float(trade["pnl_usd"])) > 0.01
        ):
            conflicts.append(trade["name"] + " / " + trade["opened_at"])
    return missing, open_trades, conflicts


def _recovery_merge(trades, historical):
    """Atomic local merge. Never delete records or overwrite closed trades."""
    inserted, completed, historical_added = 0, 0, {}
    with db() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        for trade in trades:
            existing = conn.execute(
                "SELECT id,status,pnl_usd FROM paper_v542_b "
                "WHERE address=? AND opened_at=?",
                _recovery_trade_key(trade),
            ).fetchall()
            if len(existing) > 1:
                raise RuntimeError("Dubbele lokale papertrade")
            if not existing:
                available = {
                    row[1] for row in conn.execute(
                        'PRAGMA table_info("paper_v542_b")'
                    )
                }
                columns = [
                    column for column in trade
                    if column in available and column != "id"
                ]
                names = ",".join('"' + col + '"' for col in columns)
                placeholders = ",".join("?" for _ in columns)
                conn.execute(
                    "INSERT INTO paper_v542_b (" + names + ") VALUES ("
                    + placeholders + ")",
                    [trade[col] for col in columns],
                )
                inserted += 1
            elif existing[0]["status"] == "OPEN":
                conn.execute(
                    """UPDATE paper_v542_b
                    SET status='CLOSED',pnl_usd=?,exit_reason=?,
                    closed_at=COALESCE(?,closed_at),
                    exit_market=COALESCE(?,exit_market),
                    exit_effective=COALESCE(?,exit_effective)
                    WHERE id=? AND status='OPEN'""",
                    (
                        trade["pnl_usd"], trade["exit_reason"],
                        trade["closed_at"], trade["exit_market"],
                        trade["exit_effective"], existing[0]["id"],
                    ),
                )
                completed += 1
            elif (
                existing[0]["status"] != "CLOSED"
                or existing[0]["pnl_usd"] is None
                or abs(
                    float(existing[0]["pnl_usd"]) - float(trade["pnl_usd"])
                ) > 0.01
            ):
                raise RuntimeError(
                    "Conflict bij " + trade["name"] + "; alle wijzigingen teruggedraaid"
                )

        for table, records in historical.items():
            available = {
                row[1] for row in conn.execute(
                    'PRAGMA table_info("' + table + '")'
                )
            }
            count = 0
            for record in records:
                cols = [col for col in record if col in available]
                if not cols:
                    continue
                names = ",".join('"' + col + '"' for col in cols)
                placeholders = ",".join("?" for _ in cols)
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO " + table +
                    " (" + names + ") VALUES (" + placeholders + ")",
                    [record[col] for col in cols],
                )
                count += cursor.rowcount
            historical_added[table] = count
    return inserted, completed, historical_added


def recovery_panel_v2():
    st.divider()
    st.subheader("🛟 Herstel V2 — papertrades en scanhistoriek")
    st.caption(
        "De herstelde Supabase-kopie wordt alleen ingelezen. "
        "Bij uitvoering wordt eerst een afzonderlijke veiligheidskopie geüpload. "
        "Bestaande afgesloten trades worden niet gewijzigd."
    )
    if any(st.session_state.get(key, True) for key in
           ("v542_auto", "v542_a", "v542_b")):
        st.warning("Zet automatisch scannen en paperinstappen A/B eerst UIT.")
        return

    try:
        cfg, blob, stats = _recovery_source_data()
        trades, historical = _recovery_read_source(blob)
        missing, to_complete, conflicts = _recovery_preview(trades)
        st.write(
            f"Herstelbron: {len(trades)} afgesloten trades | "
            f"ontbrekend: {missing} | open aanvullen: {to_complete} | "
            f"conflicten: {len(conflicts)}"
        )
        st.write(
            "Bron: {} scans, {} tokenmetingen".format(
                len(historical["v542_runs"]),
                len(historical["radar_v54"]),
            )
        )
        if conflicts:
            st.error("Conflicten: " + "; ".join(conflicts))
            return
    except (
        requests.RequestException, sqlite3.Error,
        RuntimeError, ValueError, OSError, TypeError,
    ) as exc:
        st.error("Voorcontrole mislukt: " + str(exc))
        return

    approved = st.checkbox(
        "Ik bevestig dat ik de gegevens wil samenvoegen",
        key="recovery_v2_approved",
    )
    if not st.button(
        "🛟 Eerst veiligheidskopie maken, dan samenvoegen",
        disabled=not approved,
        key="recovery_v2_execute",
    ):
        return

    try:
        with _LOCK:
            # Hercontrole bij uitvoeren; knop kan na een vorige scan zijn blijven staan.
            missing, to_complete, conflicts = _recovery_preview(trades)
            if conflicts:
                raise RuntimeError("Conflicten bij tweede controle: herstel gestopt")

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            unique = uuid4().hex[:8]
            prefix = "radar/recovery-safety/"
            before_name = prefix + "before_" + stamp + "_" + unique + ".db"
            merged_name = prefix + "merged_" + stamp + "_" + unique + ".db"

            before = _snapshot()
            _inspect_database(before)
            _upload_object(cfg, before_name, before, overwrite=False)

            inserted, completed, history_counts = _recovery_merge(
                trades, historical
            )
            merged = _snapshot()
            _inspect_database(merged)
            _upload_object(cfg, merged_name, merged, overwrite=False)

            try:
                remote_result = backup_remote(force=True)
            except (
                requests.RequestException, sqlite3.Error,
                RuntimeError, ValueError, OSError,
            ) as exc:
                remote_result = "Actieve back-up NIET bijgewerkt: " + str(exc)

            st.success(
                f"Samengevoegd: {inserted} trades toegevoegd en "
                f"{completed} bestaande trades afgesloten."
            )
            st.write("Veiligheidskopie:", before_name)
            st.write("Samengevoegde kopie:", merged_name)
            st.write("Actieve back-up:", remote_result)
            st.write("Historische regels toegevoegd:", history_counts)

            result = select(
                "SELECT COUNT(*),SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN status='CLOSED' THEN COALESCE(pnl_usd,0) ELSE 0 END) "
                "FROM paper_v542_b"
            )[0]
            st.info(
                f"Strategie B: {result[0]} trades, "
                f"{result[1]} afgesloten, gerealiseerde P/L "
                f"${float(result[2] or 0):+.2f}. "
                "Herstart niet zolang de actieve back-up niet bevestigd is."
            )
    except (
        requests.RequestException, sqlite3.Error,
        RuntimeError, ValueError, OSError, TypeError,
    ) as exc:
        st.error("Herstel gestopt: " + str(exc))


recovery_panel_v2()

# ============================================================
# V5.8.1 — SMART MONEY RESEARCH (APARTE SUPABASE WALLET-OPSLAG)
# ============================================================

def _v58_rpc(method, params):
    endpoint = secret('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
    if not endpoint.startswith('https://'):
        raise ValueError('SOLANA_RPC_URL moet HTTPS zijn')
    response = requests.post(endpoint,
        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=30)
    response.raise_for_status()
    result = response.json()
    if 'error' in result:
        raise RuntimeError(str(result['error']))
    return result.get('result')


def _v58_setup():
    with db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS v58_holders (
            mint TEXT NOT NULL, checked_at TEXT NOT NULL,
            token_account TEXT NOT NULL, owner TEXT,
            raw_amount TEXT NOT NULL, decimals INTEGER NOT NULL,
            quantity REAL, supply_share_pct REAL,
            PRIMARY KEY(mint,checked_at,token_account)
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_v58_mint_time
            ON v58_holders(mint,checked_at)''')



def _v581_remote(config, name):
    response = requests.get(
        _object_url(config, name),
        headers={"apikey": config[1]},
        timeout=40
    )

    if response.status_code == 404:
        return None

    if response.status_code == 400:
        try:
            error = response.json()
        except ValueError:
            response.raise_for_status()

        message = str(
            error.get("message")
            or error.get("error")
            or ""
        ).lower()

        code = str(
            error.get("code")
            or error.get("errorCode")
            or ""
        ).lower()

        if (
            "object not found" in message
            or code in ("nosuchkey", "objectnotfound")
        ):
            return None

    response.raise_for_status()

    value = response.json() if (
        response.headers.get(
            "Content-Type", ""
        ).startswith("application/json")
    ) else json.loads(response.content.decode("utf-8"))

    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
    ):
        raise ValueError(
            "Onbekend wallet-JSON-formaat"
        )

    return value

    blob = _get_object(config, name)
    if blob is None:
        return None
    value = json.loads(blob.decode('utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Onbekend wallet-JSON-formaat')
    return value


def _v581_fetch(mint):
    largest = _v58_rpc('getTokenLargestAccounts',
                      [mint, {'commitment': 'confirmed'}])
    supply = _v58_rpc('getTokenSupply',
                     [mint, {'commitment': 'confirmed'}])
    if not largest or 'value' not in largest or not supply or 'value' not in supply:
        raise RuntimeError('Onvolledig RPC-resultaat')
    raw_supply = int(supply['value']['amount'])
    if raw_supply <= 0:
        raise RuntimeError('Token heeft geen positief totaal aanbod')
    largest_accounts = largest['value'][:20]
    addresses = [x['address'] for x in largest_accounts]
    account_data = _v58_rpc('getMultipleAccounts',
            [addresses, {'encoding': 'jsonParsed', 'commitment': 'confirmed'}]) if addresses else {'value': []}
    parsed = (account_data or {}).get('value') or []
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    output = []
    for index, item in enumerate(largest_accounts):
        value = (parsed[index] or {}) if index < len(parsed) else {}
        data = value.get('data') or {}
        details = ((data.get('parsed') or {}).get('info') or {}) if isinstance(data, dict) else {}
        amount = int(item['amount'])
        decimals = int(item['decimals'])
        output.append({
            'token_account': item['address'],
            'owner': details.get('owner'),
            'raw_amount': str(amount),
            'decimals': decimals,
            'quantity': amount / 10**decimals,
            'supply_share_pct': 100 * amount / raw_supply
        })
    if not output:
        raise RuntimeError('Geen tokenaccounts gevonden')
    return {'schema_version': 1, 'mint': mint, 'checked_at': stamp,
            'accounts': output}


def _v581_store_local(snapshot):
    _v58_setup()
    rows = [(
        snapshot['mint'], snapshot['checked_at'], a['token_account'],
        a['owner'], a['raw_amount'], a['decimals'],
        a['quantity'], a['supply_share_pct']
    ) for a in snapshot['accounts']]
    with db() as conn:
        conn.executemany('''INSERT OR IGNORE INTO v58_holders
            (mint,checked_at,token_account,owner,raw_amount,decimals,quantity,supply_share_pct)
            VALUES(?,?,?,?,?,?,?,?)''', rows)


def _v581_diff(current, previous):
    if not previous or previous.get('mint') != current['mint']:
        return []
    old = {x['token_account']: x for x in previous.get('accounts', [])}
    new_ids = {x['token_account'] for x in current['accounts']}
    changes = []
    for item in current['accounts']:
        prior = old.get(item['token_account'])
        if prior is None:
            changes.append({'Tokenaccount': item['token_account'], 'Eigenaar': item['owner'],
                            'Signaal': 'Nieuw in top 20', 'Verschil tokens': None})
            continue
        raw_diff = int(item['raw_amount']) - int(prior['raw_amount'])
        if raw_diff:
            changes.append({'Tokenaccount': item['token_account'], 'Eigenaar': item['owner'],
                'Signaal': 'Toename' if raw_diff > 0 else 'Afname',
                'Verschil tokens': raw_diff / (10**int(item['decimals']))})
    for account in old:
        if account not in new_ids:
            changes.append({'Tokenaccount': account, 'Eigenaar': old[account].get('owner'),
                            'Signaal': 'Verdwenen uit top 20', 'Verschil tokens': None})
    return changes


def smart_money_panel_v581():
    _v58_setup()
    st.divider()
    st.subheader('🔎 V5.8.1 — Solana walletanalyse')
    st.caption('Onderzoek, geen tradingadvies: de grootste tokenaccounts kunnen liquiditeitspools of exchanges zijn. '
               'Een balansverschil is niet automatisch een aankoop of verkoop.')
    mint = st.text_input('Solana-token mintadres',
        value='D7eCYXewMoJ94xiXtbsiGu6XjyBEimwSpVyxYodnpump',
        key='v581_mint').strip()
    if not ADDRESS.fullmatch(mint):
        st.warning('Voer een geldig Solana-mintadres in')
        return
    cfg = _storage_config()
    if cfg is None:
        st.error('Supabase Secrets ontbreken; geen walletopslag mogelijk')
        return
    latest_name = 'radar/wallets/' + mint + '/latest.json'
    version_prefix = 'radar/wallets/' + mint + '/versions/'
    try:
        previous = _v581_remote(cfg, latest_name)
    except (requests.RequestException, ValueError, UnicodeError, KeyError) as exc:
        st.error('Vorige walletmeting niet leesbaar: ' + str(exc))
        return
    if previous:
        st.caption('Vorige veilige meting: ' + previous.get('checked_at', 'onbekend'))
    current, compare_to = previous, None
    if st.button('📡 Nieuwe walletmeting + aparte back-up', key='v581_run'):
        try:
            with st.spinner('Solana tokenaccounts controleren...'):
                fresh = _v581_fetch(mint)
                # Historische snapshot eerst apart bewaren: geen overschrijven.
                version = (version_prefix
                           + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                           + '_' + uuid4().hex[:10] + '.json')
                blob = json.dumps(fresh, ensure_ascii=False).encode('utf-8')
                _upload_object(cfg, version, blob, overwrite=False)
                # Weigeren om een nieuwere remote snapshot door een oudere te vervangen.
                latest_remote = _v581_remote(cfg, latest_name)
                if latest_remote and latest_remote.get('checked_at', '') > fresh['checked_at']:
                    raise RuntimeError('Supabase heeft inmiddels nieuwere walletgegevens')
                _upload_object(cfg, latest_name, blob, overwrite=True)
                _v581_store_local(fresh)
            st.success(f"{len(fresh['accounts'])} accounts opgeslagen in aparte Supabase walletback-up")
            st.caption('Versie: ' + version)
            current, compare_to = fresh, previous
        except (requests.RequestException, RuntimeError, sqlite3.Error, ValueError,
                KeyError, TypeError, OSError, OverflowError) as exc:
            st.error('Walletscan/back-up mislukt: ' + str(exc))
            st.caption('Als er wel een versiebestand is maar geen latest.json, blijft het versiebestand behouden.')
            return
    if not current:
        st.info('Nog geen aparte walletback-up. Start de eerste meting.')
        return
    accounts = current.get('accounts') or []
    if not accounts:
        st.warning('Wallet-back-up bevat geen accounts')
        return
    frame = pd.DataFrame(accounts)
    st.metric('Aandeel 20 grootste tokenaccounts',
              f"{sum(float(x['supply_share_pct']) for x in accounts):.2f}%")
    st.dataframe(frame, hide_index=True, width='stretch')
    changes = _v581_diff(current, compare_to)
    if changes:
        st.subheader('Veranderingen ten opzichte van vorige veilige meting')
        st.dataframe(pd.DataFrame(changes), hide_index=True, width='stretch')
    elif compare_to:
        st.caption('Geen veranderingen tussen overlappende tokenaccounts gemeten.')
    else:
        st.caption('Voor een vergelijking is een tweede meting nodig.')
    st.download_button('📥 Walletgegevens CSV', frame.to_csv(index=False).encode('utf-8'),
                       'v581_wallets.csv', 'text/csv', key='v581_download')


smart_money_panel_v581()

# ============================================================
# V5.8.2 — WALLET INTELLIGENCE
# Toevoegen onderaan de bestaande app.py
# ============================================================

_SYSTEM_PROGRAM_V582 = '11111111111111111111111111111111'


def _v582_latest_pair(mint):
    with db() as conn:
        stamps = [
            x[0] for x in conn.execute(
                'SELECT DISTINCT checked_at FROM v58_holders '
                'WHERE mint=? ORDER BY checked_at DESC LIMIT 2',
                (mint,)
            )
        ]

        data = []

        for stamp in stamps:
            rows = conn.execute(
                'SELECT token_account,owner,raw_amount,'
                'decimals,supply_share_pct '
                'FROM v58_holders '
                'WHERE mint=? AND checked_at=?',
                (mint, stamp)
            ).fetchall()

            accounts = [
                dict(
                    token_account=a,
                    owner=b,
                    raw_amount=c,
                    decimals=d,
                    supply_share_pct=e
                )
                for a, b, c, d, e in rows
            ]

            data.append((stamp, accounts))

    return data


def _v582_owner_summary(accounts):
    by_owner = {}

    for entry in accounts:
        owner = (
            entry.get('owner')
            or 'ONBEKENDE_EIGENAAR_' + entry['token_account']
        )

        group = by_owner.setdefault(
            owner,
            {
                'wallet': owner,
                'accounts': 0,
                'share': 0.0
            }
        )

        group['accounts'] += 1
        group['share'] += float(
            entry.get('supply_share_pct') or 0
        )

    return sorted(
        by_owner.values(),
        key=lambda x: x['share'],
        reverse=True
    )


def _v582_delta(current, older):
    old = {
        item['token_account']: item
        for item in older
    }

    new = {
        item['token_account']: item
        for item in current
    }

    changes = []

    for account, now in new.items():
        earlier = old.get(account)

        if earlier is None:
            changes.append({
                'Tokenaccount': account,
                'Eigenaar': now.get('owner') or 'Onbekend',
                'Type': 'Nieuw in top 20',
                'Verschil tokens': None,
                'Verschil aanbod (procentpunt)': None
            })
            continue

        if int(now['decimals']) != int(earlier['decimals']):
            changes.append({
                'Tokenaccount': account,
                'Eigenaar': now.get('owner') or 'Onbekend',
                'Type': 'Decimals veranderd: onderzoeken',
                'Verschil tokens': None,
                'Verschil aanbod (procentpunt)': None
            })
            continue

        diff = (
            int(now['raw_amount'])
            - int(earlier['raw_amount'])
        )

        if diff:
            changes.append({
                'Tokenaccount': account,
                'Eigenaar': now.get('owner') or 'Onbekend',
                'Type': (
                    'Saldo gestegen'
                    if diff > 0
                    else 'Saldo gedaald'
                ),
                'Verschil tokens': (
                    diff / (10 ** int(now['decimals']))
                ),
                'Verschil aanbod (procentpunt)': (
                    float(now['supply_share_pct'])
                    - float(earlier['supply_share_pct'])
                )
            })

    for account, item in old.items():
        if account not in new:
            changes.append({
                'Tokenaccount': account,
                'Eigenaar': item.get('owner') or 'Onbekend',
                'Type': 'Uit top 20; saldo onbekend',
                'Verschil tokens': None,
                'Verschil aanbod (procentpunt)': None
            })

    return changes


def _v582_classify_owners(owners):
    unique = [
        address for address in dict.fromkeys(owners)
        if ADDRESS.fullmatch(address)
    ]

    results = {}

    for start in range(0, len(unique), 100):
        portion = unique[start:start + 100]

        response = _v58_rpc(
            'getMultipleAccounts',
            [
                portion,
                {
                    'encoding': 'base64',
                    'commitment': 'confirmed'
                }
            ]
        )

        accounts = (response or {}).get('value')

        if accounts is None or len(accounts) != len(portion):
            raise RuntimeError(
                'Onvolledige accountinfo van RPC'
            )

        for address, value in zip(portion, accounts):
            if value is None:
                label = 'Geen accountinfo (onbekend)'

            elif value.get('executable'):
                label = 'Uitvoerbaar programma'

            elif value.get('owner') == _SYSTEM_PROGRAM_V582:
                label = (
                    'System-account '
                    '(type eigenaar niet bewezen)'
                )

            else:
                label = (
                    'Program-owned account / mogelijk PDA'
                )

            results[address] = label

    return results


def wallet_intelligence_v582():
    st.divider()
    st.subheader(
        '🧠 V5.8.2 — Walletgedrag en eigenaarsonderzoek'
    )

    st.caption(
        'Onderzoek op basis van top-20-tokenaccounts. '
        'Geen bewezen smart-moneywallets, '
        'geen gegarandeerde koop-/verkoopsignalen '
        'en geen aanpassing aan Strategie A/B.'
    )

    mint = st.text_input(
        'Tokenmint voor V5.8.2',
        value=(
            'D7eCYXewMoJ94xiXtbsiGu6XjyBEimwSpVyxYodnpump'
        ),
        key='v582_mint'
    ).strip()

    if not ADDRESS.fullmatch(mint):
        st.error('Ongeldig tokenmintadres')
        return

    try:
        observations = _v582_latest_pair(mint)

    except sqlite3.Error as exc:
        st.error(
            'Lokale walletgeschiedenis niet leesbaar: '
            + str(exc)
        )
        return

    if not observations:
        st.info(
            'Nog geen lokale walletmeting. '
            'Gebruik V5.8.1 hierboven.'
        )
        return

    stamp, current = observations[0]

    st.caption(
        'Laatste lokale meting (UTC): ' + stamp
    )

    groups = _v582_owner_summary(current)

    shares = sorted(
        [
            float(item.get('supply_share_pct') or 0)
            for item in current
        ],
        reverse=True
    )

    a, b, c, d = st.columns(4)

    a.metric(
        'Top 3 accounts',
        f'{sum(shares[:3]):.2f}%'
    )

    b.metric(
        'Top 10 accounts',
        f'{sum(shares[:10]):.2f}%'
    )

    c.metric(
        'Top 20 accounts',
        f'{sum(shares):.2f}%'
    )

    d.metric(
        'Unieke eigenaaradressen*',
        len({
            item.get('owner')
            for item in current
            if item.get('owner')
        })
    )

    st.caption(
        '*Alleen eigenaars die de RPC kon teruggeven. '
        'Niet noodzakelijk afzonderlijke personen of traders.'
    )

    st.subheader(
        'Grootste eigenaars binnen de gemeten top 20'
    )

    owner_frame = pd.DataFrame(groups).rename(
        columns={
            'wallet': 'Eigenaaradres',
            'accounts': 'Tokenaccounts in top 20',
            'share': 'Aandeel totaal aanbod (%)'
        }
    )

    st.dataframe(
        owner_frame,
        hide_index=True,
        width='stretch'
    )

    if len(observations) > 1:
        earlier_stamp, earlier = observations[1]

        changes = _v582_delta(current, earlier)

        st.subheader(
            'Verandering sinds vorige lokale meting'
        )

        st.caption(
            'Vergeleken met: ' + earlier_stamp + ' UTC. '
            'Een verdwenen account hoeft niets verkocht te hebben.'
        )

        if changes:
            frame = pd.DataFrame(changes)

            st.dataframe(
                frame,
                hide_index=True,
                width='stretch'
            )

            st.download_button(
                '📥 Balansverschillen CSV',
                frame.to_csv(index=False).encode('utf-8'),
                'v582_wallet_changes.csv',
                'text/csv',
                key='v582_delta_csv'
            )

        else:
            st.info(
                'Geen verschillen in de overlappende '
                'top-20-tokenaccounts gevonden.'
            )

    else:
        st.info(
            'Voor historische vergelijking zijn minimaal '
            'twee lokale snapshots nodig.'
        )

    st.subheader('Onderzoek eigenaaradressen')

    st.caption(
        'Optioneel extra RPC-verzoek. '
        'Dit identificeert nog geen specifieke '
        'liquiditeitspools of exchanges.'
    )

    owners = [
        group['wallet']
        for group in groups
        if ADDRESS.fullmatch(group['wallet'])
    ]

    if st.button(
        '🔍 Controleer eigenaarstypes',
        key='v582_classify'
    ):
        try:
            labels = _v582_classify_owners(owners)
            st.session_state[
                'v582_owner_labels_' + mint
            ] = labels

        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError
        ) as exc:
            st.error(
                'Eigenaarstypecontrole mislukt: '
                + str(exc)
            )

    known = st.session_state.get(
        'v582_owner_labels_' + mint,
        {}
    )

    if known:
        st.dataframe(
            pd.DataFrame([
                {
                    'Eigenaaradres': address,
                    'Accounttype': account_type
                }
                for address, account_type in known.items()
            ]),
            hide_index=True,
            width='stretch'
        )

    if owners:
        chosen = st.selectbox(
            'Adres voor beperkte transactiecontrole',
            owners,
            key='v582_owner'
        )

        if st.button(
            '📜 Bekijk 10 recente transactiehandtekeningen',
            key='v582_signatures'
        ):
            try:
                records = _v58_rpc(
                    'getSignaturesForAddress',
                    [
                        chosen,
                        {
                            'limit': 10,
                            'commitment': 'confirmed'
                        }
                    ]
                )

                if not records:
                    st.info(
                        'Geen recente transactiehandtekeningen gevonden.'
                    )

                else:
                    transactions = []

                    for item in records:
                        block_time = item.get('blockTime')

                        transactions.append({
                            'Signature': item.get('signature'),
                            'UTC': (
                                datetime.fromtimestamp(
                                    block_time,
                                    timezone.utc
                                ).isoformat()
                                if block_time else None
                            ),
                            'Status': (
                                'Fout' if item.get('err')
                                else 'Succes'
                            )
                        })

                    st.dataframe(
                        pd.DataFrame(transactions),
                        hide_index=True,
                        width='stretch'
                    )

                    st.caption(
                        'Transactiehandtekeningen tonen activiteit, '
                        'geen gerealiseerde handelswinst.'
                    )

            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError
            ) as exc:
                st.error(
                    'Transactiecontrole mislukt: '
                    + str(exc)
                )


wallet_intelligence_v582()

from v593_dashboard import v593_background_dashboard
v593_background_dashboard()

# ============================================================
# V6.0 — SIGNAL DIAGNOSTICS
# Alleen lezen: geen wijzigingen aan trades of database
# ============================================================

def v60_block_reason(outcome, reason):
    text = str(reason or "").lower()

    if outcome == "PAPER_ENTRY":
        return "Paperinstap uitgevoerd"

    if outcome == "UITGESCHAKELD":
        return "Strategie uitgeschakeld"

    if "al eens virtueel verhandeld" in text:
        return "Token eerder verhandeld"

    if "te_weinig_metingen" in text:
        return "Te weinig metingen"

    if "ongeldig_interval" in text:
        return "Verkeerde meetintervallen"

    if "onvolledige_data" in text:
        return "Onvolledige marktdata"

    if "geen_handel" in text:
        return "Geen actieve handel"

    if text.startswith(("a faalt", "b faalt")):
        return "Momentum of volume onvoldoende"

    if "liquiditeit" in text:
        return "Onvoldoende liquiditeit"

    if "ouder dan 24" in text:
        return "Handelspaar te oud"

    if "kooptransacties" in text:
        return "Onvoldoende koopdruk"

    if "microtransacties" in text:
        return "Mogelijke botactiviteit"

    if "maximaal" in text and "posities" in text:
        return "Maximum open posities"

    if outcome == "KANDIDAAT":
        return "Kandidaat"

    return "Andere reden"


def v60_diagnostics_panel():
    st.divider()
    st.subheader("🔬 V6.0 — Waarom geen nieuwe instappen?")

    st.caption(
        "Analyse van de laatste 20 Streamlit-scans. "
        "Toont de eerste blokkade per beslissing. "
        "Strategie A en B blijven ongewijzigd."
    )

    try:
        recent = select(
            "SELECT started_at FROM v542_runs "
            "ORDER BY started_at DESC LIMIT 20"
        )

        if not recent:
            st.info("Nog geen scans om te analyseren.")
            return

        stamps = [row[0] for row in recent]
        marks = ",".join("?" for _ in stamps)

        query = (
            "SELECT scan_at, address, name, strategy, "
            "outcome, reason "
            "FROM v542_decisions "
            f"WHERE scan_at IN ({marks}) "
            "ORDER BY scan_at DESC"
        )

        rows = select(query, tuple(stamps))

    except sqlite3.Error as exc:
        st.error("Database kon niet worden gelezen: " + str(exc))
        return

    if not rows:
        st.warning(
            "Geen beslissingen in de laatste scans gevonden. "
            "Controleer of de Streamlit-scanner actief scant."
        )
        return

    df = pd.DataFrame(
        rows,
        columns=[
            "Tijd UTC",
            "Tokenadres",
            "Naam",
            "Strategie",
            "Uitkomst",
            "Reden"
        ]
    )

    df["Blokkade"] = [
        v60_block_reason(outcome, reason)
        for outcome, reason
        in zip(df["Uitkomst"], df["Reden"])
    ]

    a, b, c = st.columns(3)

    a.metric("Scans onderzocht", len(stamps))
    b.metric("Unieke tokens", df["Tokenadres"].nunique())
    c.metric(
        "Paperinstappen",
        int((df["Uitkomst"] == "PAPER_ENTRY").sum())
    )

    strategy = st.radio(
        "Strategie",
        ["A", "B", "Allebei"],
        horizontal=True,
        key="v60_strategy"
    )

    filtered = (
        df if strategy == "Allebei"
        else df[df["Strategie"] == strategy]
    )

    if filtered.empty:
        st.info("Geen beslissingen voor deze strategie.")
        return

    summary = (
        filtered.groupby(
            ["Strategie", "Blokkade"]
        )
        .size()
        .reset_index(name="Aantal")
        .sort_values("Aantal", ascending=False)
    )

    st.write("**Belangrijkste redenen voor geen instap**")

    st.dataframe(
        summary,
        hide_index=True,
        width='stretch'
    )

    st.bar_chart(
        summary.groupby("Blokkade")["Aantal"].sum()
    )

    st.write("**Laatste individuele beslissingen**")

    details = filtered.copy()

    details["Tijd België"] = (
        pd.to_datetime(details["Tijd UTC"], utc=True)
        .dt.tz_convert("Europe/Brussels")
        .dt.strftime("%d-%m %H:%M")
    )

    st.dataframe(
        details[
            [
                "Tijd België",
                "Naam",
                "Tokenadres",
                "Strategie",
                "Uitkomst",
                "Blokkade",
                "Reden"
            ]
        ].head(100),
        hide_index=True,
        width='stretch'
    )

    st.download_button(
        "📥 Download diagnose CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        "v60_diagnostics.csv",
        "text/csv",
        key="v60_csv"
    )

    st.caption(
        "Tellingen zijn beslissingen over meerdere scans, "
        "niet unieke handelsmogelijkheden. "
        "De GitHub-achtergrondengine wordt hier "
        "nog niet afzonderlijk geanalyseerd."
    )


v60_diagnostics_panel()

# ============================================================
# V6.4 - WALLETINTELLIGENCE + GITHUB PAPERPOSITIONS
# Plak dit ONDERAAN de bestaande app.py. Niets verwijderen.
# Gebruikt bestaande _v582_latest_pair(), db() en GitHub-lezer.
# Alleen lezen: geen trades, DB-migraties of RPC-transacties.
# ============================================================

def v64_wallet_paper_panel():
    import math
    from datetime import datetime, timezone
    from v593_dashboard import _v593_read, _v593_token

    st.divider()
    st.subheader('🧠 V6.4 — Walletinformatie bij papertrades')
    st.caption(
        'Combineert bestaande lokale walletmetingen met de afzonderlijke '
        'GitHub-paperportefeuille. Geen automatisch koopadvies of live walletfeed.'
    )

    if st.button('🔄 GitHub- en walletoverzicht vernieuwen', key='v64_refresh'):
        _v593_read.clear()

    try:
        state = _v593_read('data/v591/portfolio.json', _v593_token())
        report = _v593_read('data/v591/preview.json', _v593_token())
    except Exception as exc:
        st.warning('GitHub-papergegevens niet beschikbaar: ' + str(exc))
        return

    opened = []
    for strategy in ('A', 'B', 'C'):
        for mint, trade in (state.get('open', {}).get(strategy) or {}).items():
            opened.append((strategy, mint, trade))

    run = report.get('run') or {}
    rejected = run.get('rejected') or {}
    c1, c2, c3 = st.columns(3)
    c1.metric('GitHub-paperposities open', len(opened))
    c2.metric('Geen momentum (laatste run)', rejected.get('no_momentum_signal', 0))
    c3.metric('Signalen (laatste run)', run.get('signals', 0))

    # De beschikbare walletmetingen zijn niet automatisch in de
    # GitHub collector DB aanwezig. Toon dus niet-gemeten expliciet.
    try:
        with db() as connection:
            available = connection.execute(
                'SELECT mint, MAX(checked_at) FROM v58_holders '
                'GROUP BY mint ORDER BY MAX(checked_at) DESC LIMIT 30'
            ).fetchall()
    except Exception as exc:
        st.warning('Lokale walletgeschiedenis niet leesbaar: ' + str(exc))
        return

    selected = {mint for _, mint, _ in opened}
    # Ook reeds gemeten tokens zonder open papertrade blijven zichtbaar.
    selected.update(row[0] for row in available)
    # Beperk de tabel tot open posities en maximaal 30 lokaal gevolgde tokens.
    meta = {}
    for strategy, mint, trade in opened:
        meta.setdefault(mint, []).append(strategy)

    def safe_float(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0

    rows = []
    for mint in sorted(selected, key=lambda x: (x not in meta, x)):
        strategies = '/'.join(meta.get(mint, [])) or 'Geen open positie'
        try:
            observations = _v582_latest_pair(mint)
        except Exception as exc:
            rows.append({
                'Tokenmint': mint, 'Open strategie': strategies,
                'Walletstatus': 'Leesfout: ' + str(exc)[:65],
                'Laatste walletmeting': '', 'Top 3 %': None,
                'Top 20 %': None, 'Saldo veranderingen': None,
                'Unieke eigenaars (top 20)': None,
            })
            continue

        if not observations:
            rows.append({
                'Tokenmint': mint, 'Open strategie': strategies,
                'Walletstatus': 'NIET GEMETEN',
                'Laatste walletmeting': '', 'Top 3 %': None,
                'Top 20 %': None, 'Saldo veranderingen': None,
                'Unieke eigenaars (top 20)': None,
            })
            continue

        stamp, accounts = observations[0]
        shares = sorted(
            (safe_float(x.get('supply_share_pct')) for x in accounts),
            reverse=True,
        )
        owners = len({x.get('owner') for x in accounts if x.get('owner')})
        changes = None
        if len(observations) > 1:
            _, older = observations[1]
            before = {x['token_account']: x for x in older}
            changes = sum(
                1 for x in accounts
                if x['token_account'] in before
                and x.get('raw_amount') != before[x['token_account']].get('raw_amount')
            )

        try:
            measured = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if measured.tzinfo is None:
                measured = measured.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - measured).total_seconds() / 3600
            status = 'VEROUDERD (>24u)' if age_hours > 24 else 'BESCHIKBAAR'
        except (ValueError, TypeError, OverflowError):
            status = 'TIJD ONBEKEND'

        rows.append({
            'Tokenmint': mint,
            'Open strategie': strategies,
            'Walletstatus': status,
            'Laatste walletmeting': stamp,
            'Top 3 %': round(sum(shares[:3]), 2),
            'Top 20 %': round(sum(shares), 2),
            'Saldo veranderingen': changes,
            'Unieke eigenaars (top 20)': owners,
        })

    if not rows:
        st.info('Nog geen paperposities of lokaal gemeten walletgegevens.')
        return

    frame = pd.DataFrame(rows)
    st.dataframe(frame, hide_index=True, width='stretch')
    st.download_button(
        '📥 Paper + walletanalyse CSV',
        frame.to_csv(index=False).encode('utf-8'),
        'v64_wallet_paperanalyse.csv',
        'text/csv',
        key='v64_wallet_csv',
    )
    st.caption(
        'Top-3/top-20 = aandeel van grootste TOKENACCOUNTS in totale supply, '
        'niet noodzakelijk onafhankelijke wallets. '
        'Saldo veranderingen zijn geen bewezen aan- of verkopen. '
        'Onbekende, oude of ontbrekende metingen blokkeren geen bestaande papertrades.'
    )


v64_wallet_paper_panel()


# ============================================================
# V6.8 — PERFORMANCE & LEARNING (READ-ONLY)
# GitHub A/B/C-paperstate blijft gescheiden van lokale Streamlit-data.
# Geen writes naar GitHub, Supabase, portefeuille of learning.db.
# ============================================================


def _v68_number(value):
    import math
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _v68_closed_metrics(closed):
    """Equity en drawdown uit gerealiseerde P/L, niet uit open marks."""
    ordered = sorted(closed, key=lambda row: str(row.get('closed_at') or ''))
    pnl = [_v68_number(row.get('pnl_usd')) for row in ordered]
    pnl = [value for value in pnl if value is not None]
    total = sum(pnl)
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    equity, high, max_drawdown = 0.0, 0.0, 0.0
    curve = []
    for value in pnl:
        equity += value
        high = max(high, equity)
        max_drawdown = max(max_drawdown, high - equity)
        curve.append(equity)
    return {
        'count': len(pnl), 'pnl': total,
        'winrate': 100 * len(wins) / len(pnl) if pnl else None,
        'avg_pnl': total / len(pnl) if pnl else None,
        'avg_win': sum(wins) / len(wins) if wins else None,
        'avg_loss': sum(losses) / len(losses) if losses else None,
        'max_drawdown_usd': max_drawdown if pnl else None,
        'curve': curve,
    }


def _v68_strategy_table(state):
    rows = []
    for strat in ('A', 'B', 'C'):
        closed = state['closed'].get(strat) or []
        metrics = _v68_closed_metrics(closed)
        rows.append({
            'Strategie': strat,
            'Open': len(state['open'].get(strat) or {}),
            'Gesloten': metrics['count'],
            'Gerealiseerde P/L $': round(metrics['pnl'], 2),
            'Winrate %': round(metrics['winrate'], 1) if metrics['winrate'] is not None else None,
            'Gem. P/L $': round(metrics['avg_pnl'], 2) if metrics['avg_pnl'] is not None else None,
            'Max. gerealiseerde drawdown $': (
                round(metrics['max_drawdown_usd'], 2)
                if metrics['max_drawdown_usd'] is not None else None
            ),
            'Voorlopige evaluatie': (
                'Nog geen gesloten trades' if metrics['count'] == 0
                else 'Kleine steekproef' if metrics['count'] < 30
                else 'Eerste evaluatie mogelijk (geen garantie)'
            ),
        })
    return rows


def _v68_learning_upload(uploaded, state):
    """Analyses uploaded learning.db read-only; never alters source bytes."""
    if uploaded.size > 100_000_000:
        raise ValueError('learning.db groter dan 100 MB; upload geweigerd')
    content = uploaded.getvalue()
    if not content.startswith(b'SQLite format 3\x00'):
        raise ValueError('Geen geldige SQLite-bestandsheader')
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        tmp.write(content)
        tmp.flush()
        with sqlite3.connect(tmp.name, timeout=20) as conn:
            conn.execute('PRAGMA query_only=ON')
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite-integriteitscontrole mislukt')
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'outcomes' not in tables or 'snapshots' not in tables:
                raise ValueError('De tabellen outcomes en snapshots ontbreken')
            overall = conn.execute("""
                SELECT horizon_minutes, COUNT(*),
                       AVG(return_pct),
                       AVG(CASE WHEN return_pct > 0 THEN 1.0 ELSE 0.0 END),
                       AVG(CASE WHEN crash_30pct = 1 THEN 1.0 ELSE 0.0 END)
                  FROM outcomes
                 WHERE horizon_minutes IN (15,30,60)
                 GROUP BY horizon_minutes
                 ORDER BY horizon_minutes
            """).fetchall()
            baselines = []
            for strat in ('A', 'B', 'C'):
                for address, position in (state['open'].get(strat) or {}).items():
                    baselines.append((strat, address, position))
                for position in (state['closed'].get(strat) or []):
                    baselines.append((strat, position.get('token'), position))
            cohort = {}
            for strat, address, position in baselines:
                pair, opened = position.get('pair'), position.get('opened_at')
                if not address or not pair or not opened:
                    continue
                for horizon in (15, 30, 60):
                    observed = conn.execute("""
                        SELECT return_pct
                          FROM outcomes
                         WHERE token_address=? AND pair_address=?
                           AND horizon_minutes=?
                           AND ABS((julianday(baseline_at) - julianday(?)) * 1440) <= 10
                         ORDER BY ABS((julianday(baseline_at) - julianday(?)) * 1440) ASC
                         LIMIT 1
                    """, (address, pair, horizon, opened, opened)).fetchone()
                    if observed is not None:
                        value = _v68_number(observed[0])
                        if value is not None:
                            cohort.setdefault((strat, horizon), []).append(value)
            cohort_rows = [
                {'Strategie': strat, 'Horizon (min)': horizon,
                 'Gem. koersrendement %': round(sum(values)/len(values), 2),
                 'Gematchte instappen': len(values)}
                for (strat, horizon), values in sorted(cohort.items())
            ]
            overall_rows = [
                {'Horizon (min)': int(horizon), 'Aantal labels': count,
                 'Gem. koersrendement %': round(avg, 2) if avg is not None else None,
                 'Positieve labels %': round(100 * positive, 1) if positive is not None else None,
                 'Crash ≥30% %': round(100 * crash, 1) if crash is not None else None}
                for horizon, count, avg, positive, crash in overall
            ]
    return overall_rows, cohort_rows


@st.fragment(run_every='120s')
def v68_performance_dashboard():
    from v593_dashboard import _v593_read, _v593_token, _v593_validate, _v593_time, _v593_age

    st.divider()
    st.subheader('📊 Performance & Learning V6.8 — A / B / C')
    st.caption('Alle GitHub-data wordt alleen gelezen. Geen live-orders, geen automatische '
               'aanpassing van strategieën en geen samenvoeging met de Streamlit/Supabase-portefeuilles.')

    if st.button('🔄 Performancegegevens vernieuwen', key='v68_refresh'):
        _v593_read.clear()

    try:
        read_token = _v593_token()
        state = _v593_read('data/v591/portfolio.json', read_token)
        report = _v593_read('data/v591/preview.json', read_token)
        history = _v593_read('data/v594/scan_history.json', read_token)
        latest = _v593_read('data/latest_scan.json', read_token)
        _v593_validate(state, report)
        if not isinstance(history.get('runs'), list):
            raise ValueError('Scan history bevat geen runs-lijst')
    except (requests.RequestException, FileNotFoundError, RuntimeError,
            ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        st.error('Performancegegevens konden niet worden gelezen: ' + str(exc))
        st.info('Controleer GitHub Actions en de JSON-bestanden. Bij een private '
                'repository is een read-only GITHUB_READ_TOKEN nodig.')
        return

    when = report.get('generated_utc')
    age = _v593_age(when)
    st.caption('Laatste GitHub-paperupdate: ' + _v593_time(when))
    if age is None or age < -5 or age > 20:
        st.warning('De papergegevens zijn mogelijk verouderd of hebben een ongeldige tijdstempel.')
    if report.get('last_processed') != state.get('cursor'):
        st.warning('Paperportfolio en preview zijn tijdelijk niet synchroon.')

    rows = _v68_strategy_table(state)
    a, b, c, d = st.columns(4)
    a.metric('Open paperposities', sum(r['Open'] for r in rows))
    b.metric('Gesloten papertrades', sum(r['Gesloten'] for r in rows))
    c.metric('Gerealiseerde P/L', f"${sum(r['Gerealiseerde P/L $'] for r in rows):+.2f}")
    d.metric('Leerlabels totaal', latest.get('total_labels', '—'))
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption('Winrate en drawdown verschijnen pas met gesloten trades. '
               'Drawdown is uitsluitend berekend uit gerealiseerde P/L en mist '
               'tussentijdse verliezen op open posities.')

    opened = []
    for strategy in ('A', 'B', 'C'):
        for address, position in (state['open'].get(strategy) or {}).items():
            opened.append({
                'Strategie': strategy,
                'Token': position.get('name', 'Onbekend'),
                'Adres': address,
                'Pair': position.get('pair'),
                'Geopend (BE)': _v593_time(position.get('opened_at')),
                'Instapprijs $': _v68_number(position.get('entry_market')),
                'Hoeveelheid': _v68_number(position.get('quantity')),
                'Huidige P/L': 'Onbekend — geen actuele uitvoerbare verkoopquote',
            })
    st.subheader('📍 Open A/B/C-paperposities')
    if opened:
        st.dataframe(pd.DataFrame(opened), hide_index=True, use_container_width=True)
    else:
        st.info('Geen open GitHub-paperposities.')

    closed = []
    for strategy in ('A', 'B', 'C'):
        closed.extend([{'Strategie': strategy, 'Gesloten UTC': t.get('closed_at'),
                        'P/L $': _v68_number(t.get('pnl_usd'))}
                       for t in state['closed'].get(strategy) or []])
    if closed:
        curve = pd.DataFrame(closed).sort_values('Gesloten UTC')
        curve['Gecumuleerde gerealiseerde P/L $'] = curve['P/L $'].fillna(0).cumsum()
        st.line_chart(curve.set_index('Gesloten UTC')[['Gecumuleerde gerealiseerde P/L $']])

    st.subheader('📡 Betrouwbaarheid van de automatische scans')
    recent = history['runs'][-30:]
    if recent:
        scan = pd.DataFrame([{
            'UTC': r.get('finished_at_utc') or r.get('started_at_utc'),
            'Metingen': r.get('recorded', 0),
            'Signalen': r.get('signals', 0),
            'Fouten': r.get('error_count', 0),
            'Status': r.get('status', 'Onbekend'),
        } for r in recent])
        st.line_chart(scan.set_index('UTC')[['Metingen', 'Signalen']])
        st.dataframe(scan.tail(15).iloc[::-1], hide_index=True, use_container_width=True)
        st.caption('Meer metingen betekenen betere observatie, niet automatisch betere trades.')

    st.subheader('🧠 Uitkomsten na 15 / 30 / 60 minuten')
    st.caption('Voor de volledige leeranalyse is een export van data/learning.db nodig. '
               'Het bestand wordt tijdelijk en alleen-lezen verwerkt, niet geüpload naar GitHub.')
    uploaded = st.file_uploader('Learning database (optioneel)', type=['db'], key='v68_learning_db')
    if uploaded is not None:
        try:
            baseline_rows, cohort_rows = _v68_learning_upload(uploaded, state)
            st.write('**Alle verzamelde koerslabels (geen onafhankelijk geteste handelsresultaten)**')
            st.dataframe(pd.DataFrame(baseline_rows), hide_index=True, use_container_width=True)
            st.write('**Koerslabels rond A/B/C-paperinstappen (indien beschikbaar)**')
            if cohort_rows:
                st.dataframe(pd.DataFrame(cohort_rows), hide_index=True, use_container_width=True)
            else:
                st.info('Nog geen passende 15/30/60-minutenlabels rond de paperinstappen.')
            st.caption('Dezelfde token kan meerdere gecorreleerde labels bijdragen. '
                       'Een gematcht koersrendement is geen uitgevoerde swap, '
                       'geen portefeuille-P/L en geen bewijs van voorspelkracht.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error('Learning database kon niet worden geanalyseerd: ' + str(exc))
    else:
        st.info('GitHub-paperprestaties en scanbetrouwbaarheid verschijnen hierboven automatisch. '
                'Voeg learning.db toe voor de extra labelanalyse.')


v68_performance_dashboard()
