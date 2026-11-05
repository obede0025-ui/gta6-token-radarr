
"""GTA narrative radar V5.4: persistent discovery and simulated trades only."""
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GTA 6 Radar V5.4",
    page_icon="🎮",
    layout="wide"
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
DEX = "https://api.dexscreener.com"

TERMS = [
    "GTA6", "GTA VI", "Grand Theft Auto",
    "Vice City", "Leonida", "Lucia Camino",
    "Jason Duval", "Port Gellhorn"
]

STRONG = [
    "gta6", "gta 6", "gta vi",
    "grand theft auto", "grandtheftauto"
]

RELATED = [
    "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn",
    "raul bautista", "ambrosia"
]

ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6-Radar-Research/5.4"
})


# ==================================================
# HELPERS
# ==================================================

def utcnow():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def number(x):
    try:
        return float(x) if x is not None else None
    except (ValueError, TypeError):
        return None


def n(x):
    return number(x) or 0.0


def contains(text, terms):
    text = str(text or "").lower()

    return any(
        re.search(
            r"(?<![a-z0-9])"
            + re.escape(word)
            + r"(?![a-z0-9])",
            text
        )
        for word in terms
    )


# ==================================================
# DATABASE
# ==================================================

def database():
    connection = sqlite3.connect(DB, timeout=20)
    connection.execute("PRAGMA busy_timeout=20000")
    return connection


def rows(sql, params=()):
    with database() as con:
        return con.execute(sql, params).fetchall()


def initialize():
    # Existing database tables are preserved.
    sqls = [
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
            first_seen TEXT NOT NULL,
            last_checked TEXT,
            last_found TEXT,
            missed_scans INTEGER NOT NULL DEFAULT 0,
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
            status TEXT NOT NULL DEFAULT 'OPEN',
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
        CREATE TABLE IF NOT EXISTS v54_runs (
            scanned_at TEXT PRIMARY KEY,
            discovered_count INTEGER,
            fetched_count INTEGER,
            errors INTEGER,
            entries INTEGER,
            exits INTEGER
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_v54_history
        ON radar_v54(address, scanned_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_paper_v54_status
        ON paper_v54(status)
        """
    ]

    with database() as con:
        for statement in sqls:
            con.execute(statement)


# ==================================================
# API
# ==================================================

def get_json(url, params=None):
    response = HTTP.get(
        url,
        params=params,
        timeout=18
    )

    response.raise_for_status()
    return response.json()


# ==================================================
# GTA TOKEN DISCOVERY
# ==================================================

def discover():
    found = {}
    errors = []

    def add(address, source, text=""):
        if (
            not isinstance(address, str)
            or not ADDR.fullmatch(address)
        ):
            return

        item = found.setdefault(
            address,
            {"sources": set(), "extra": ""}
        )

        item["sources"].add(source)

        item["extra"] = (
            item["extra"] + " " + str(text)
        )[:2000]

    # Keyword search
    for term in TERMS:
        try:
            data = get_json(
                DEX + "/latest/dex/search",
                {"q": term}
            )

            for pair in data.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}

                title = (
                    f"{base.get('name', '')} "
                    f"{base.get('symbol', '')}"
                )

                if contains(title, STRONG + RELATED):
                    add(
                        base.get("address"),
                        "DEX search"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Search {term}: {exc}"
            )

    # Latest profiles and boosts
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]

    for endpoint in endpoints:
        try:
            data = get_json(DEX + endpoint)

            for item in (
                data if isinstance(data, list) else []
            ):
                if item.get("chainId") != "solana":
                    continue

                extra = (
                    str(item.get("description") or "")
                    + " "
                    + " ".join(
                        str(link.get("url") or "")
                        for link in item.get("links") or []
                    )
                )

                # Ignore unrelated boosted tokens.
                if contains(extra, STRONG + RELATED):
                    add(
                        item.get("tokenAddress"),
                        "DEX profile",
                        extra
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Profiles: {exc}"
            )

    # Historical tracking
    historical = rows(
        """
        SELECT address FROM discovered
        ORDER BY last_seen ASC
        """
    )

    watch = rows(
        """
        SELECT address FROM watchlist
        ORDER BY added_at DESC
        """
    )

    priority = rows(
        """
        SELECT address FROM tracking_state
        ORDER BY COALESCE(last_checked, first_seen) ASC
        """
    )

    for (address,) in historical + watch + priority:
        if (
            isinstance(address, str)
            and ADDR.fullmatch(address)
        ):
            found.setdefault(
                address,
                {"sources": set(), "extra": ""}
            )["sources"].add(
                "Historisch gevolgd"
            )

    return found, errors


# ==================================================
# MARKET DATA
# ==================================================

def fetch_pairs(addresses):
    selected = {}
    successful = set()
    errors = []

    for offset in range(0, len(addresses), 30):
        batch = addresses[offset:offset + 30]

        try:
            data = get_json(
                DEX + "/tokens/v1/solana/"
                + ",".join(batch)
            )

            if not isinstance(data, list):
                raise ValueError(
                    "Unexpected token-pairs response"
                )

            successful.update(batch)

            for pair in data:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address")

                if address not in batch:
                    continue

                liquidity = n(
                    (pair.get("liquidity") or {}).get("usd")
                )

                old = selected.get(address)

                old_liquidity = n(
                    (old.get("liquidity") or {}).get("usd")
                ) if old else 0

                if (
                    old is None
                    or liquidity > old_liquidity
                ):
                    selected[address] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Batch {offset // 30 + 1}: {exc}"
            )

    return selected, successful, errors


# ==================================================
# MOMENTUM DETECTION
# ==================================================

def momentum(address, stamp, price, volume):
    previous = rows(
        """
        SELECT scanned_at, price, volume_m5
        FROM radar_v54
        WHERE address=?
        ORDER BY scanned_at DESC
        LIMIT 2
        """,
        (address,)
    )

    if len(previous) < 2:
        return 0, "Nog geen 3 V5.4-metingen"

    newest, oldest = previous[0], previous[1]

    try:
        gap1 = (
            datetime.fromisoformat(stamp)
            - datetime.fromisoformat(newest[0])
        ).total_seconds() / 60

        gap2 = (
            datetime.fromisoformat(newest[0])
            - datetime.fromisoformat(oldest[0])
        ).total_seconds() / 60

    except (TypeError, ValueError):
        return 0, "Ongeldige meettijden"

    if not (
        4 <= gap1 <= 12
        and 4 <= gap2 <= 12
    ):
        return 0, "Scaninterval niet 4-12 minuten"

    measurements = [
        price,
        volume,
        newest[1],
        newest[2],
        oldest[1],
        oldest[2]
    ]

    if not all(n(x) > 0 for x in measurements):
        return 0, "Prijs/volume ontbreekt in een meting"

    prices_rise = (
        oldest[1] < newest[1] < price
        and price >= oldest[1] * 1.02
    )

    volumes_rise = (
        oldest[2] < newest[2] < volume
        and volume >= 250
    )

    if prices_rise and volumes_rise:
        return (
            80,
            "3 stijgende prijs- en 5m-volumemetingen"
        )

    if prices_rise or volumes_rise:
        return (
            30,
            "Slechts 1 van de 2 trendvoorwaarden"
        )

    return 0, "Geen opwaartse bevestiging"


# ==================================================
# TOKEN ANALYSIS
# ==================================================

def assess(address, pair, meta, stamp):
    token = pair.get("baseToken") or {}

    name = str(token.get("name") or "Unknown")
    symbol = str(token.get("symbol") or "?")

    info = pair.get("info") or {}

    context = meta["extra"] + " " + " ".join(
        str(x.get("url") or "")
        for x in (
            (info.get("websites") or [])
            + (info.get("socials") or [])
        )
    )

    headline = name + " " + symbol

    score = (
        100 if contains(headline, STRONG)
        else 65 if contains(headline, RELATED)
        else 75 if contains(context, STRONG)
        else 35 if contains(context, RELATED)
        else 0
    )

    created = number(pair.get("pairCreatedAt"))

    age = (
        max(
            0,
            (
                datetime.fromisoformat(stamp).timestamp()
                * 1000 - created
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

    tx = (
        (pair.get("txns") or {}).get("m5") or {}
    )

    buys = int(n(tx.get("buys")))
    sells = int(n(tx.get("sells")))

    strength, evidence = momentum(
        address,
        stamp,
        price,
        volume
    )

    warnings = []

    if liquidity is None:
        warnings.append("Liquiditeit ontbreekt")

    elif liquidity < 10000:
        warnings.append("Liquiditeit < $10.000")

    if (
        volume is not None
        and buys + sells >= 100
        and volume / (buys + sells) < 3
    ):
        warnings.append(
            "Veel kleine transacties: mogelijk bots"
        )

    warnings.append(
        "Holders, mint/freeze en verkoopbaarheid "
        "niet gecontroleerd"
    )

    if (
        strength == 80
        and liquidity is not None
        and liquidity >= 10000
        and score >= 65
    ):
        phase = "MOMENTUM TER CONTROLE"

    elif (
        age is not None
        and age < 24
        and score >= 65
    ):
        phase = "PRE-TREND KANDIDAAT"

    else:
        phase = "MONITOREN"

    return {
        "Adres": address,
        "Naam": name,
        "Ticker": symbol,
        "Bron": ", ".join(sorted(meta["sources"])),
        "Narrative": score,
        "Leeftijd": (
            round(age, 2)
            if age is not None else None
        ),
        "Prijs": price,
        "Liquiditeit": liquidity,
        "Volume5m": volume,
        "Kopen5m": buys,
        "Verkopen5m": sells,
        "Momentum": strength,
        "Bewijs": evidence,
        "Fase": phase,
        "Risico": "; ".join(warnings),
        "Pair": pair.get("pairAddress") or ""
    }


# ==================================================
# SAVE SCAN RESULTS
# ==================================================

def store_scan(results, missing, stamp):
    with database() as con:
        for r in results:
            address = r["Adres"]

            con.execute(
                """
                INSERT INTO discovered
                (address,name,symbol,first_seen,last_seen)
                VALUES(?,?,?,?,?)
                ON CONFLICT(address) DO UPDATE SET
                name=excluded.name,
                symbol=excluded.symbol,
                last_seen=excluded.last_seen
                """,
                (
                    address,
                    r["Naam"],
                    r["Ticker"],
                    stamp,
                    stamp
                )
            )

            con.execute(
                """
                INSERT INTO tracking_state
                (address,first_seen,last_checked,
                 last_found,missed_scans,status)
                VALUES(?,?,?,?,0,'ACTIEF')
                ON CONFLICT(address) DO UPDATE SET
                last_checked=excluded.last_checked,
                last_found=excluded.last_found,
                missed_scans=0,
                status='ACTIEF'
                """,
                (
                    address,
                    stamp,
                    stamp,
                    stamp
                )
            )

            con.execute(
                """
                INSERT OR REPLACE INTO radar_v54
                (address,scanned_at,name,symbol,
                 pair_address,source,price,liquidity,
                 volume_m5,buys_m5,sells_m5,age_hours,
                 narrative,momentum,evidence,phase,warnings)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    address,
                    stamp,
                    r["Naam"],
                    r["Ticker"],
                    r["Pair"],
                    r["Bron"],
                    r["Prijs"],
                    r["Liquiditeit"],
                    r["Volume5m"],
                    r["Kopen5m"],
                    r["Verkopen5m"],
                    r["Leeftijd"],
                    r["Narrative"],
                    r["Momentum"],
                    r["Bewijs"],
                    r["Fase"],
                    r["Risico"]
                )
            )

        for address in missing:
            con.execute(
                """
                UPDATE tracking_state
                SET last_checked=?,
                    missed_scans=missed_scans+1,
                    status='NIET_GEVONDEN'
                WHERE address=?
                """,
                (stamp, address)
            )


# ==================================================
# PAPER TRADING SETTINGS
# ==================================================

def default_settings():
    return {
        "amount": 25.0,
        "fee": 0.01,
        "slip": 0.02,
        "minimum": 10000.0,
        "take": 0.30,
        "stop": 0.15,
        "hours": 4.0
    }


# ==================================================
# AUTO PAPER TRADING ENGINE
# ==================================================

def paper_step(observations, stamp, enabled, config):
    by_address = {
        r["Adres"]: r
        for r in observations
    }

    entries = 0
    exits = 0

    with database() as con:
        opened = con.execute(
            """
            SELECT id,address,entry_market,quantity,
                   amount_usd,fee_rate,slippage_rate,
                   opened_at,peak_mark
            FROM paper_v54
            WHERE status='OPEN'
            """
        ).fetchall()

        open_addresses = {
            trade[1] for trade in opened
        }

        # ------------------------------------------
        # VIRTUAL EXITS
        # ------------------------------------------

        for trade in opened:
            (
                trade_id,
                address,
                entry,
                qty,
                amount,
                fee,
                slip,
                opened_at,
                peak
            ) = trade

            r = by_address.get(address)

            # Never use an old price for a new exit.
            if (
                not r
                or not r["Prijs"]
                or r["Prijs"] <= 0
            ):
                continue

            price = r["Prijs"]
            peak = max(n(peak), price)

            con.execute(
                """
                UPDATE paper_v54
                SET last_mark=?,
                    last_mark_at=?,
                    peak_mark=?
                WHERE id=?
                """,
                (
                    price,
                    stamp,
                    peak,
                    trade_id
                )
            )

            elapsed = (
                datetime.fromisoformat(stamp)
                - datetime.fromisoformat(opened_at)
            ).total_seconds() / 3600

            change = price / entry - 1

            if change >= config["take"]:
                reason = "TAKE_PROFIT"

            elif change <= -config["stop"]:
                reason = "STOP_LOSS"

            elif elapsed >= config["hours"]:
                reason = "TIME_EXIT"

            else:
                reason = None

            if reason is None:
                continue

            # Do not invent a sell fill without liquidity.
            if (
                r["Liquiditeit"] is None
                or r["Liquiditeit"] < config["minimum"]
            ):
                con.execute(
                    """
                    UPDATE paper_v54
                    SET exit_reason=?
                    WHERE id=?
                    """,
                    (
                        "EXIT_BLOCKED_LIQUIDITY",
                        trade_id
                    )
                )

                continue

            effective = price * (1 - slip)
            proceeds = qty * effective * (1 - fee)

            con.execute(
                """
                UPDATE paper_v54
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
                    effective,
                    proceeds - amount,
                    reason,
                    trade_id
                )
            )

            exits += 1

        # ------------------------------------------
        # AUTOMATIC VIRTUAL ENTRIES
        # ------------------------------------------

        if enabled:
            for r in observations:
                address = r["Adres"]

                if (
                    address in open_addresses
                    or r["Fase"] != "MOMENTUM TER CONTROLE"
                ):
                    continue

                if (
                    r["Narrative"] < 65
                    or not r["Prijs"]
                    or r["Prijs"] <= 0
                    or r["Liquiditeit"] is None
                    or r["Liquiditeit"] < config["minimum"]
                ):
                    continue

                # One auto-paper position per token.
                exists = con.execute(
                    """
                    SELECT 1 FROM paper_v54
                    WHERE address=?
                    LIMIT 1
                    """,
                    (address,)
                ).fetchone()

                if exists:
                    continue

                # Filter possible bot-heavy micro-trading.
                txcount = (
                    r["Kopen5m"]
                    + r["Verkopen5m"]
                )

                if (
                    txcount >= 100
                    and r["Volume5m"] is not None
                    and r["Volume5m"] / txcount < 3
                ):
                    continue

                effective = (
                    r["Prijs"]
                    * (1 + config["slip"])
                )

                qty = (
                    config["amount"]
                    * (1 - config["fee"])
                    / effective
                )

                con.execute(
                    """
                    INSERT INTO paper_v54
                    (address,name,pair_address,opened_at,
                     entry_market,entry_effective,
                     amount_usd,quantity,fee_rate,
                     slippage_rate,status,last_mark,
                     last_mark_at,peak_mark)
                    VALUES(
                        ?,?,?,?,?,?,?,?,?,?,
                        'OPEN',?,?,?
                    )
                    """,
                    (
                        address,
                        r["Naam"],
                        r["Pair"],
                        stamp,
                        r["Prijs"],
                        effective,
                        config["amount"],
                        qty,
                        config["fee"],
                        config["slip"],
                        r["Prijs"],
                        stamp,
                        r["Prijs"]
                    )
                )

                open_addresses.add(address)
                entries += 1

    return entries, exits


# ==================================================
# COMPLETE SCAN
# ==================================================

def scan(enabled, config):
    stamp = utcnow()

    discovered, errors = discover()

    addresses = list(discovered)

    # Open positions always have tracking priority.
    paper_addresses = [
        r[0] for r in rows(
            """
            SELECT address FROM paper_v54
            WHERE status='OPEN'
            """
        )
    ]

    addresses = list(
        dict.fromkeys(
            paper_addresses + addresses
        )
    )

    market, queried, market_errors = fetch_pairs(
        addresses
    )

    errors.extend(market_errors)

    results = []

    for address, pair in market.items():
        try:
            r = assess(
                address,
                pair,
                discovered[address],
                stamp
            )

            if (
                r["Narrative"] >= 65
                or address in paper_addresses
            ):
                results.append(r)

        except (
            KeyError,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Analyse {address[:9]}: {exc}"
            )

    missing = [
        address
        for address in queried - set(market)
        if rows(
            """
            SELECT 1 FROM tracking_state
            WHERE address=?
            """,
            (address,)
        )
    ]

    store_scan(
        results,
        missing,
        stamp
    )

    entries, exits = paper_step(
        results,
        stamp,
        enabled,
        config
    )

    with database() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO v54_runs
            (scanned_at,discovered_count,fetched_count,
             errors,entries,exits)
            VALUES(?,?,?,?,?,?)
            """,
            (
                stamp,
                len(discovered),
                len(results),
                len(errors),
                entries,
                exits
            )
        )

    return (
        pd.DataFrame(results),
        errors,
        stamp,
        entries,
        exits
    )


# ==================================================
# LOAD SAVED DATA
# ==================================================

def latest_saved():
    last = rows(
        """
        SELECT scanned_at
        FROM radar_v54
        ORDER BY scanned_at DESC
        LIMIT 1
        """
    )

    if not last:
        return pd.DataFrame()

    saved = rows(
        """
        SELECT address,name,symbol,source,
               narrative,age_hours,price,
               liquidity,volume_m5,buys_m5,
               sells_m5,momentum,evidence,
               phase,warnings,pair_address
        FROM radar_v54
        WHERE scanned_at=?
        """,
        (last[0][0],)
    )

    return pd.DataFrame(
        saved,
        columns=[
            "Adres",
            "Naam",
            "Ticker",
            "Bron",
            "Narrative",
            "Leeftijd",
            "Prijs",
            "Liquiditeit",
            "Volume5m",
            "Kopen5m",
            "Verkopen5m",
            "Momentum",
            "Bewijs",
            "Fase",
            "Risico",
            "Pair"
        ]
    )


# ==================================================
# STREAMLIT DASHBOARD
# ==================================================

initialize()

st.title("🎮 GTA 6 Narrative Trading Radar V5.4")

st.caption(
    "Solana | Persistent Tracking | "
    "Momentum | Automatische PAPER trading"
)

st.warning(
    "Uitsluitend virtuele transacties. "
    "DexScreener-prijzen zijn geen uitvoerbare offertes. "
    "Rug pulls, wash trading en verkopen die mislukken "
    "kunnen in deze simulatie onvoldoende zichtbaar zijn."
)

st.sidebar.header("⚙️ Instellingen")

auto_scan = st.sidebar.toggle(
    "Scannen elke 5 min (actieve browser)",
    value=False
)

auto_paper = st.sidebar.toggle(
    "Automatische PAPER trading",
    value=False
)

min_liq = st.sidebar.number_input(
    "Minimum liquiditeit ($)",
    min_value=1000.0,
    max_value=1000000.0,
    value=10000.0,
    step=1000.0
)

amount = st.sidebar.number_input(
    "Virtuele inzet ($)",
    min_value=5.0,
    max_value=1000.0,
    value=25.0,
    step=5.0
)

fee = st.sidebar.number_input(
    "Fee per zijde (%)",
    min_value=0.0,
    max_value=10.0,
    value=1.0,
    step=0.25
)

slip = st.sidebar.number_input(
    "Slippage per zijde (%)",
    min_value=0.0,
    max_value=30.0,
    value=2.0,
    step=0.5
)

take = st.sidebar.number_input(
    "Virtuele take profit (%)",
    min_value=5,
    max_value=500,
    value=30,
    step=5
)

stop = st.sidebar.number_input(
    "Virtuele stop loss (%)",
    min_value=5,
    max_value=95,
    value=15,
    step=5
)

hours = st.sidebar.number_input(
    "Tijdslimiet (uren)",
    min_value=1,
    max_value=72,
    value=4,
    step=1
)

config = {
    "amount": amount,
    "fee": fee / 100,
    "slip": slip / 100,
    "minimum": min_liq,
    "take": take / 100,
    "stop": stop / 100,
    "hours": float(hours)
}

st.sidebar.caption(
    "De instellingen gelden voor nieuwe posities. "
    "Bestaande posities behouden hun instapkosten. "
    "Exitdrempels volgen de huidige instellingen."
)

defaults = {
    "v54_last_attempt": 0.0,
    "v54_df": pd.DataFrame(),
    "v54_time": "Nog niet",
    "v54_errors": []
}

for key, initial in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = initial


# ==================================================
# LIVE DASHBOARD
# ==================================================

@st.fragment(run_every="300s")
def dashboard():
    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    due = (
        auto_scan
        and time.monotonic()
        - st.session_state.v54_last_attempt >= 300
    )

    if manual or due:
        st.session_state.v54_last_attempt = (
            time.monotonic()
        )

        with st.spinner(
            "Nieuwe en historische tokens onderzoeken..."
        ):
            try:
                (
                    data,
                    errors,
                    stamp,
                    entries,
                    exits
                ) = scan(auto_paper, config)

                st.session_state.v54_df = data
                st.session_state.v54_time = stamp
                st.session_state.v54_errors = errors

                st.success(
                    f"Scan afgerond: {len(data)} tokens, "
                    f"{entries} virtuele instappen, "
                    f"{exits} virtuele uitstappen"
                )

            except (
                requests.RequestException,
                sqlite3.Error,
                ValueError
            ) as exc:
                st.error(f"Scanfout: {exc}")

    df = st.session_state.v54_df

    if df.empty:
        df = latest_saved()

    st.caption(
        "Laatste scan van deze sessie (UTC): "
        f"{st.session_state.v54_time}"
    )

    a, b, c, d = st.columns(4)

    a.metric(
        "Tokens in overzicht",
        len(df)
    )

    b.metric(
        "Momentum 80",
        int((df["Momentum"] == 80).sum())
        if not df.empty else 0
    )

    c.metric(
        "Gevolgde tokens",
        rows(
            "SELECT COUNT(*) FROM discovered"
        )[0][0]
    )

    d.metric(
        "Open paper trades",
        rows(
            """
            SELECT COUNT(*) FROM paper_v54
            WHERE status='OPEN'
            """
        )[0][0]
    )

    # ------------------------------------------
    # EARLY DISCOVERY
    # ------------------------------------------

    st.subheader("🚀 Nieuwe GTA-paren")

    if not df.empty:
        young = df[
            df["Leeftijd"].notna()
            & (df["Leeftijd"] < 24)
        ]

        st.dataframe(
            young.sort_values("Leeftijd"),
            use_container_width=True,
            hide_index=True
        )

        # --------------------------------------
        # MOMENTUM DASHBOARD
        # --------------------------------------

        st.subheader(
            "📈 Alle relevante GTA-tokens"
        )

        st.dataframe(
            df.sort_values(
                ["Momentum", "Volume5m"],
                ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )

        options = {
            f"{r['Naam']} — {r['Adres'][:10]}": r["Adres"]
            for _, r in df.iterrows()
        }

        choice = st.selectbox(
            "Token bekijken",
            list(options)
        )

        address = options[choice]

        token = df[
            df["Adres"] == address
        ].iloc[0]

        st.code(address, language="text")

        st.write(
            "**Trendsignaal:**",
            token["Bewijs"]
        )

        st.write(
            "**Risico:**",
            token["Risico"]
        )

        pair = token["Pair"] or address

        st.link_button(
            "Open DexScreener",
            "https://dexscreener.com/solana/" + pair
        )

        if st.button("⭐ Watchlist"):
            with database() as con:
                con.execute(
                    """
                    INSERT OR IGNORE INTO watchlist
                    (address,name,added_at)
                    VALUES(?,?,?)
                    """,
                    (
                        address,
                        token["Naam"],
                        utcnow()
                    )
                )

            st.success(
                "Aan watchlist toegevoegd"
            )

        # --------------------------------------
        # PRICE AND VOLUME CHARTS
        # --------------------------------------

        history = rows(
            """
            SELECT scanned_at,price,volume_m5
            FROM radar_v54
            WHERE address=?
            ORDER BY scanned_at DESC
            LIMIT 100
            """,
            (address,)
        )

        if len(history) > 1:
            chart = pd.DataFrame(
                history,
                columns=[
                    "Tijd",
                    "Prijs",
                    "Volume5m"
                ]
            )

            chart["Tijd"] = pd.to_datetime(
                chart["Tijd"],
                utc=True
            )

            chart = (
                chart.sort_values("Tijd")
                .set_index("Tijd")
            )

            st.line_chart(
                chart[["Prijs"]]
            )

            st.line_chart(
                chart[["Volume5m"]]
            )

        st.download_button(
            "📥 CSV-export",
            df.to_csv(index=False).encode(),
            "gta6_v54.csv",
            mime="text/csv"
        )

    else:
        st.info(
            "Klik op Scan nu om te beginnen."
        )

    # ------------------------------------------
    # PAPER TRADING RESULTS
    # ------------------------------------------

    st.subheader(
        "🧪 Automatische paper trading"
    )

    trades = rows(
        """
        SELECT id,name,address,opened_at,
               amount_usd,entry_market,last_mark,
               status,pnl_usd,exit_reason
        FROM paper_v54
        ORDER BY id DESC
        LIMIT 200
        """
    )

    if trades:
        all_trades = pd.DataFrame(
            trades,
            columns=[
                "ID",
                "Token",
                "Adres",
                "Geopend",
                "Inzet $",
                "Instapprijs",
                "Laatste prijs",
                "Status",
                "Resultaat $",
                "Exitreden"
            ]
        )

        st.dataframe(
            all_trades,
            hide_index=True,
            use_container_width=True
        )

        closed = all_trades[
            all_trades["Status"] == "CLOSED"
        ]

        if not closed.empty:
            x, y, z = st.columns(3)

            x.metric(
                "Afgesloten",
                len(closed)
            )

            y.metric(
                "Netto P/L",
                f"${closed['Resultaat $'].sum():,.2f}"
            )

            z.metric(
                "Winstpercentage trades",
                f"{100 * (closed['Resultaat $'] > 0).mean():.1f}%"
            )

    else:
        st.info(
            "Nog geen automatische virtuele "
            "posities geregistreerd."
        )

    # ------------------------------------------
    # API ERRORS
    # ------------------------------------------

    if st.session_state.v54_errors:
        with st.expander(
            "⚠️ API-meldingen "
            f"({len(st.session_state.v54_errors)})"
        ):
            st.write(
                st.session_state.v54_errors[:30]
            )


dashboard()

st.caption(
    "Streamlit Community Cloud kan pauzeren. "
    "SQLite is op deze hosting niet gegarandeerd "
    "permanent. Voor continue scans is een aparte "
    "worker met externe database noodzakelijk. "
    "Er worden geen echte orders uitgevoerd."
)
