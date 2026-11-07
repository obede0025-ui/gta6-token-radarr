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
DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
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
