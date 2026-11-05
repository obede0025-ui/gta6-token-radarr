
import streamlit as st
import requests
import pandas as pd
import sqlite3
import time
import os
import re
from datetime import datetime, timezone

# ============================================================
# GTA 6 TOKEN RADAR v5.0
# Read-only research tool - geen automatische transacties
# ============================================================

st.set_page_config(
    page_title="GTA 6 Token Radar v5.0",
    page_icon="🎮",
    layout="wide"
)

API = "https://api.dexscreener.com"
RPC_URL = "https://api.mainnet-beta.solana.com"
DB = os.environ.get("GTA6_RADAR_DB", "gta6_radar.db")
SCAN_SECONDS = 300

# ============================================================
# GTA 6 NARRATIVE DATABASE
# ============================================================

STRONG_TERMS = [
    "gta6", "gta 6", "gta vi",
    "grand theft auto", "grandtheftauto",
    "grand-theft-auto"
]

LOCATION_TERMS = [
    "vice city", "leonida", "port gellhorn",
    "ambrosia", "grassrivers", "mount kalaga"
]

CHARACTER_TERMS = [
    "jason duval", "lucia camino", "cal hampton",
    "boobie ike", "drequan priest", "raul bautista",
    "brian heder"
]

SEARCH_TERMS = [
    "GTA6",
    "GTA 6",
    "GTA VI",
    "Grand Theft Auto",
    "Vice City",
    "Leonida",
    "Port Gellhorn",
    "Ambrosia"
]

# ============================================================
# DATABASE
# ============================================================

def db_execute(query, params=()):
    with sqlite3.connect(DB) as con:
        con.execute(query, params)


def db_fetchall(query, params=()):
    with sqlite3.connect(DB) as con:
        return con.execute(query, params).fetchall()


def init_db():
    # Bestaande tabellen blijven behouden.
    db_execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            address TEXT,
            scanned_at TEXT,
            price REAL,
            liquidity REAL,
            volume24 REAL,
            buys24 INTEGER,
            sells24 INTEGER
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS discovered (