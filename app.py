
"""
GTA 6 Narrative Radar V5.4.1
Persistent Tracking + Momentum + Automatic Paper Trading

Timezone:
- Database: UTC
- Dashboard: Europe/Brussels

No real blockchain transactions.
"""

import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="GTA 6 Radar V5.4.1",
    page_icon="🎮",
    layout="wide"
)

APP_VERSION = "5.4.1"

DB = os.getenv(
    "GTA6_RADAR_DB",
    "gta6_radar.db"
)

API = "https://api.dexscreener.com"

BELGIUM = ZoneInfo("Europe/Brussels")

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

ADDRESS = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": "GTA6ResearchRadar/5.4.1",
    "Accept": "application/json"
})


# ============================================================
# 2. DATE AND TIME HELPERS
# ============================================================

def now():
    """UTC timestamp for database storage."""
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def dt(value):
    """Read an ISO timestamp, interpreting naive values as UTC."""
    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def belgian_time(value):
    """Convert UTC database timestamp to Belgium local time."""
    if not value:
        return "Onbekend"

    return dt(value).astimezone(BELGIUM).strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def belgian_clock(value):
    """Format a datetime for Belgium."""
    return value.astimezone(BELGIUM).strftime(
        "%H:%M:%S"
    )


def num(value):
    try:
        return float(value) if value is not None else None

    except (ValueError, TypeError):
        return None


def n(value):
    return num(value) or 0.0


def match(value, terms):
    text = str(value or "").lower()

    return any(
        re.search(
            r"(?<![a-z0-9])"
            + re.escape(term)
            + r"(?![a-z0-9])",
            text
        )
        for term in terms
    )


# ============================================================
# 3. DATABASE
# ============================================================

def conn():
    connection = sqlite3.connect(
        DB,
        timeout=25
    )

    connection.execute(
        "PRAGMA busy_timeout=25000"
    )

    return connection


def query(sql, args=()):
    with conn() as connection:
        return connection.execute(
            sql,
            args
        ).fetchall()


def setup():
    """
    Create missing tables.
    Existing data is preserved.
    """

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
        CREATE TABLE IF NOT EXISTS v541_config (
            name TEXT PRIMARY KEY,
            value TEXT
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

    with conn() as connection:
        for statement in statements:
            connection.execute(statement)


def config_get(key, default):
    result = query(
        "SELECT value FROM v541_config WHERE name=?",
        (key,)
    )

    if not result:
        return default

    return result[0][0] == "1"


def config_set(key, value):
    with conn() as connection:
        connection.execute(
            """
            INSERT INTO v541_config(name, value)
            VALUES(?, ?)
            ON CONFLICT(name)
            DO UPDATE SET value=excluded.value
            """,
            (
                key,
                "1" if value else "0"
            )
        )


# ============================================================
# 4. API CONNECTION
# ============================================================

def fetch(url, params=None):
    response = HTTP.get(
        url,
        params=params,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# 5. TOKEN DISCOVERY
# ============================================================

def discovery():
    found = {}
    errors = []

    def add(address, source):
        if (
            isinstance(address, str)
            and ADDRESS.fullmatch(address)
        ):
            found.setdefault(
                address,
                set()
            ).add(source)

    # DexScreener keyword discovery
    for word in SEARCHES:

        try:
            data = fetch(
                API + "/latest/dex/search",
                {"q": word}
            )

            for pair in data.get("pairs") or []:

                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}

                title = (
                    str(base.get("name") or "")
                    + " "
                    + str(base.get("symbol") or "")
                )

                if match(title, STRONG + RELATED):

                    add(
                        base.get("address"),
                        "DEX search"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:

            errors.append(
                f"Search {word}: {error}"
            )

    # Latest token profiles and boosts
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]

    for endpoint in endpoints:

        try:
            data = fetch(API + endpoint)

            if not isinstance(data, list):
                continue

            for item in data:

                if item.get("chainId") != "solana":
                    continue

                description = str(
                    item.get("description") or ""
                )

                links = " ".join(
                    str(link.get("url") or "")
                    for link in item.get("links") or []
                )

                context = description + " " + links

                if match(
                    context,
                    STRONG + RELATED
                ):
                    add(
                        item.get("tokenAddress"),
                        "DEX profile"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:

            errors.append(
                f"Profile {endpoint}: {error}"
            )

    # Always prioritize tokens with open paper positions
    priority = [
        row[0]
        for row in query(
            """
            SELECT address
            FROM paper_v54
            WHERE status='OPEN'
            """
        )
    ]

    # Watchlist
    priority += [
        row[0]
        for row in query(
            """
            SELECT address
            FROM watchlist
            ORDER BY added_at DESC
            """
        )
    ]

    # Previously tracked tokens
    priority += [
        row[0]
        for row in query(
            """
            SELECT address
            FROM tracking_state
            ORDER BY COALESCE(
                last_checked,
                first_seen
            ) ASC
            LIMIT 250
            """
        )
    ]

    # Older discoveries
    priority += [
        row[0]
        for row in query(
            """
            SELECT address
            FROM discovered
            ORDER BY last_seen ASC
            LIMIT 250
            """
        )
    ]

    for address in priority:

        if (
            isinstance(address, str)
            and ADDRESS.fullmatch(address)
        ):

            found.setdefault(
                address,
                set()
            ).add(
                "Historisch gevolgd"
            )

    return found, errors


# ============================================================
# 6. MARKET DATA
# ============================================================

def prices(addresses):
    best = {}
    successful = set()
    errors = []

    for offset in range(
        0,
        len(addresses),
        30
    ):

        batch = addresses[
            offset:offset + 30
        ]

        try:
            data = fetch(
                API + "/tokens/v1/solana/"
                + ",".join(batch)
            )

            if not isinstance(data, list):
                raise ValueError(
                    "Onverwacht API-antwoord"
                )

            successful.update(batch)

            for pair in data:

                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}

                address = base.get("address")

                if address not in batch:
                    continue

                liquidity = n(
                    (pair.get("liquidity") or {})
                    .get("usd")
                )

                old = best.get(address)

                old_liquidity = (
                    n(
                        (old.get("liquidity") or {})
                        .get("usd")
                    )
                    if old
                    else 0
                )

                if (
                    old is None
                    or liquidity > old_liquidity
                ):
                    best[address] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:

            errors.append(
                f"Market batch {offset // 30 + 1}: "
                f"{error}"
            )

    return best, successful, errors


# ============================================================
# 7. MOMENTUM DETECTION
# ============================================================

def trend(
    address,
    stamp,
    price,
    volume
):
    """
    Evaluate the latest three valid observations.

    Allowed scan gaps: 3.5 to 20 minutes.

    This is a research signal, not a buy recommendation.
    """

    older = query(
        """
        SELECT
            scanned_at,
            price,
            volume_m5,
            pair_address
        FROM radar_v54
        WHERE address=?
        AND scanned_at<?
        ORDER BY scanned_at DESC
        LIMIT 12
        """,
        (
            address,
            stamp
        )
    )

    valid = [
        (time_value, p, v, pair)
        for time_value, p, v, pair in older
        if n(p) > 0 and n(v) > 0
    ]

    if (
        price is None
        or volume is None
        or price <= 0
        or volume <= 0
    ):
        return (
            0,
            "Prijs of 5m-volume ontbreekt",
            "ONVOLLEDIGE_DATA",
            None
        )

    if len(valid) < 2:
        return (
            0,
            f"{len(valid) + 1}/3 geldige metingen",
            "TE_WEINIG_METINGEN",
            None
        )

    newest = valid[0]
    oldest = valid[1]

    gap_a = (
        dt(stamp)
        - dt(newest[0])
    ).total_seconds() / 60

    gap_b = (
        dt(newest[0])
        - dt(oldest[0])
    ).total_seconds() / 60

    timing = (
        f"Tussenpozen: "
        f"{gap_b:.1f} min en "
        f"{gap_a:.1f} min"
    )

    if not (
        3.5 <= gap_a <= 20
        and 3.5 <= gap_b <= 20
    ):
        return (
            0,
            timing + " — ongeldig tijdsvenster",
            "ONGELDIG_INTERVAL",
            (gap_b, gap_a)
        )

    rising_price = (
        oldest[1] < newest[1] < price
        and price >= oldest[1] * 1.02
    )

    rising_volume = (
        oldest[2] < newest[2] < volume
        and volume >= 250
    )

    if rising_price and rising_volume:

        return (
            80,
            timing
            + " — prijs en 5m-volume stijgen",
            "MOMENTUM",
            (gap_b, gap_a)
        )

    if rising_price or rising_volume:

        return (
            30,
            timing
            + " — slechts één trendvoorwaarde",
            "GEDEELTELIJK",
            (gap_b, gap_a)
        )

    return (
        0,
        timing + " — geen opwaartse trend",
        "GEEN_TREND",
        (gap_b, gap_a)
    )


# ============================================================
# 8. TOKEN ANALYSIS
# ============================================================

def assess(
    address,
    pair,
    sources,
    stamp
):
    base = pair.get("baseToken") or {}

    name = str(
        base.get("name") or "Unknown"
    )

    symbol = str(
        base.get("symbol") or "?"
    )

    title = name + " " + symbol

    narrative = (
        100 if match(title, STRONG)
        else 65 if match(title, RELATED)
        else 0
    )

    created = num(
        pair.get("pairCreatedAt")
    )

    if created:
        age = max(
            0,
            (
                dt(stamp).timestamp() * 1000
                - created
            ) / 3600000
        )
    else:
        age = None

    price = num(
        pair.get("priceUsd")
    )

    liquidity = num(
        (pair.get("liquidity") or {})
        .get("usd")
    )

    volume = num(
        (pair.get("volume") or {})
        .get("m5")
    )

    transactions = (
        (pair.get("txns") or {})
        .get("m5") or {}
    )

    buys = int(
        n(transactions.get("buys"))
    )

    sells = int(
        n(transactions.get("sells"))
    )

    (
        momentum,
        evidence,
        diagnostic,
        gaps
    ) = trend(
        address,
        stamp,
        price,
        volume
    )

    warnings = []

    if liquidity is None:
        warnings.append(
            "Liquiditeit onbekend"
        )

    elif liquidity < 10000:
        warnings.append(
            "Liquiditeit onder $10.000"
        )

    total_transactions = buys + sells

    if (
        total_transactions >= 100
        and volume is not None
        and volume / total_transactions < 3
    ):
        warnings.append(
            "Kleine gemiddelde transactie; "
            "bots mogelijk"
        )

    warnings.append(
        "Holders, mint/freeze en verkoopbaarheid "
        "niet geverifieerd"
    )

    if (
        momentum == 80
        and liquidity is not None
        and liquidity >= 10000
        and narrative >= 65
    ):
        phase = "MOMENTUM TER CONTROLE"

    elif (
        age is not None
        and age < 24
    ):
        phase = "PRE-TREND KANDIDAAT"

    else:
        phase = "MONITOREN"

    return {
        "address": address,
        "name": name,
        "symbol": symbol,
        "sources": ", ".join(
            sorted(sources)
        ),
        "narrative": narrative,
        "age": age,
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "momentum": momentum,
        "evidence": evidence,
        "diagnostic": diagnostic,
        "gaps": gaps,
        "phase": phase,
        "warnings": "; ".join(warnings),
        "pair": pair.get("pairAddress") or ""
    }


# ============================================================
# 9. SAVE MARKET OBSERVATIONS
# ============================================================

def save_observations(
    records,
    missing,
    stamp
):
    with conn() as connection:

        for r in records:

            connection.execute(
                """
                INSERT INTO discovered(
                    address,
                    name,
                    symbol,
                    first_seen,
                    last_seen
                )
                VALUES(?,?,?,?,?)

                ON CONFLICT(address)
                DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
                """,
                (
                    r["address"],
                    r["name"],
                    r["symbol"],
                    stamp,
                    stamp
                )
            )

            connection.execute(
                """
                INSERT INTO tracking_state(
                    address,
                    first_seen,
                    last_checked,
                    last_found,
                    missed_scans,
                    status
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
                    r["address"],
                    stamp,
                    stamp,
                    stamp
                )
            )

            connection.execute(
                """
                INSERT OR REPLACE INTO radar_v54(
                    address,
                    scanned_at,
                    name,
                    symbol,
                    pair_address,
                    source,
                    price,
                    liquidity,
                    volume_m5,
                    buys_m5,
                    sells_m5,
                    age_hours,
                    narrative,
                    momentum,
                    evidence,
                    phase,
                    warnings
                )
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    r["address"],
                    stamp,
                    r["name"],
                    r["symbol"],
                    r["pair"],
                    r["sources"],
                    r["price"],
                    r["liquidity"],
                    r["volume"],
                    r["buys"],
                    r["sells"],
                    r["age"],
                    r["narrative"],
                    r["momentum"],
                    r["evidence"],
                    r["phase"],
                    r["warnings"]
                )
            )

        for address in missing:

            connection.execute(
                """
                UPDATE tracking_state
                SET
                    last_checked=?,
                    missed_scans=missed_scans+1,
                    status='NIET_GEVONDEN'
                WHERE address=?
                """,
                (
                    stamp,
                    address
                )
            )


# ============================================================
# 10. PAPER TRADING DECISION ENGINE
# ============================================================

def decide(
    token,
    settings,
    used_addresses
):
    if token["address"] in used_addresses:
        return (
            "GEEN_INSTAP",
            "Token is al eerder virtueel verhandeld"
        )

    if token["narrative"] < 65:
        return (
            "GEEN_INSTAP",
            "Onvoldoende GTA-relevantie"
        )

    if token["momentum"] != 80:
        return (
            "GEEN_INSTAP",
            token["diagnostic"]
            + ": "
            + token["evidence"]
        )

    if (
        token["price"] is None
        or token["price"] <= 0
    ):
        return (
            "GEBLOKKEERD",
            "Ongeldige prijs"
        )

    if (
        token["liquidity"] is None
        or token["liquidity"] < settings["minimum"]
    ):
        return (
            "GEBLOKKEERD",
            "Liquiditeit te laag of onbekend"
        )

    if (
        token["age"] is None
        or token["age"] > 24
    ):
        return (
            "GEBLOKKEERD",
            "Handelspaar ouder dan 24 uur "
            "of leeftijd onbekend"
        )

    if (
        token["volume"] is None
        or token["volume"] < 250
    ):
        return (
            "GEBLOKKEERD",
            "Onvoldoende vijfminutenvolume"
        )

    total_transactions = (
        token["buys"]
        + token["sells"]
    )

    if (
        total_transactions >= 100
        and token["volume"] / total_transactions < 3
    ):
        return (
            "GEBLOKKEERD",
            "Veel microtransacties: "
            "gemiddeld minder dan $3"
        )

    if token["buys"] <= token["sells"]:
        return (
            "GEBLOKKEERD",
            "Niet meer koop- dan verkooptransacties"
        )

    return (
        "KANDIDAAT",
        "Momentum en kwantitatieve filters voldaan; "
        "contractveiligheid onbekend"
    )


# ============================================================
# 11. AUTOMATIC PAPER TRADING
# ============================================================

def paper_engine(
    records,
    stamp,
    enabled,
    settings
):
    found = {
        r["address"]: r
        for r in records
    }

    entries = 0
    exits = 0

    with conn() as connection:

        open_trades = connection.execute(
            """
            SELECT
                id,
                address,
                opened_at,
                entry_market,
                quantity,
                amount_usd,
                fee_rate,
                slippage_rate
            FROM paper_v54
            WHERE status='OPEN'
            """
        ).fetchall()

        used_addresses = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT address
                FROM paper_v54
                """
            ).fetchall()
        }

        # ------------------------------------------------
        # VIRTUAL EXITS
        # ------------------------------------------------

        for trade in open_trades:

            (
                trade_id,
                address,
                opened_at,
                entry_price,
                quantity,
                amount,
                fee,
                slippage
            ) = trade

            token = found.get(address)

            if (
                not token
                or token["price"] is None
                or token["price"] <= 0
            ):
                continue

            current_price = token["price"]

            connection.execute(
                """
                UPDATE paper_v54
                SET
                    last_mark=?,
                    last_mark_at=?,
                    peak_mark=MAX(
                        COALESCE(peak_mark, 0),
                        ?
                    )
                WHERE id=?
                """,
                (
                    current_price,
                    stamp,
                    current_price,
                    trade_id
                )
            )

            price_change = (
                current_price / entry_price - 1
            )

            elapsed_hours = (
                dt(stamp)
                - dt(opened_at)
            ).total_seconds() / 3600

            if price_change >= settings["take"]:
                reason = "TAKE_PROFIT"

            elif price_change <= -settings["stop"]:
                reason = "STOP_LOSS"

            elif elapsed_hours >= settings["hours"]:
                reason = "TIME_EXIT"

            else:
                reason = None

            if reason is None:
                continue

            # A missing liquidity reading must not
            # be treated as proof of an executable exit.
            if (
                token["liquidity"] is None
                or token["liquidity"] < settings["minimum"]
            ):
                connection.execute(
                    """
                    UPDATE paper_v54
                    SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                    WHERE id=?
                    """,
                    (trade_id,)
                )

                continue

            effective_exit = (
                current_price * (1 - slippage)
            )

            proceeds = (
                quantity
                * effective_exit
                * (1 - fee)
            )

            pnl = proceeds - amount

            connection.execute(
                """
                UPDATE paper_v54
                SET
                    status='CLOSED',
                    closed_at=?,
                    exit_market=?,
                    exit_effective=?,
                    pnl_usd=?,
                    exit_reason=?
                WHERE id=?
                AND status='OPEN'
                """,
                (
                    stamp,
                    current_price,
                    effective_exit,
                    pnl,
                    reason,
                    trade_id
                )
            )

            exits += 1

        # ------------------------------------------------
        # AUTOMATIC VIRTUAL ENTRIES
        # ------------------------------------------------

        for token in records:

            outcome, explanation = decide(
                token,
                settings,
                used_addresses
            )

            if (
                outcome == "KANDIDAAT"
                and not enabled
            ):
                outcome = "UITGESCHAKELD"
                explanation = (
                    "Automatische PAPER trading staat uit"
                )

            if (
                outcome == "KANDIDAAT"
                and enabled
            ):
                effective_entry = (
                    token["price"]
                    * (1 + settings["slip"])
                )

                quantity = (
                    settings["amount"]
                    * (1 - settings["fee"])
                    / effective_entry
                )

                connection.execute(
                    """
                    INSERT INTO paper_v54(
                        address,
                        name,
                        pair_address,
                        opened_at,
                        entry_market,
                        entry_effective,
                        amount_usd,
                        quantity,
                        fee_rate,
                        slippage_rate,
                        status,
                        last_mark,
                        last_mark_at,
                        peak_mark
                    )
                    VALUES(
                        ?,?,?,?,?,?,?,?,?,?,
                        'OPEN',?,?,?
                    )
                    """,
                    (
                        token["address"],
                        token["name"],
                        token["pair"],
                        stamp,
                        token["price"],
                        effective_entry,
                        settings["amount"],
                        quantity,
                        settings["fee"],
                        settings["slip"],
                        token["price"],
                        stamp,
                        token["price"]
                    )
                )

                used_addresses.add(
                    token["address"]
                )

                entries += 1

                outcome = "PAPER_ENTRY"

                explanation = (
                    "Virtuele instap geregistreerd. "
                    "Geen echte blockchaintransactie."
                )

            connection.execute(
                """
                INSERT OR REPLACE INTO v541_decisions(
                    scan_at,
                    address,
                    name,
                    outcome,
                    reason,
                    price,
                    liquidity
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    stamp,
                    token["address"],
                    token["name"],
                    outcome,
                    explanation,
                    token["price"],
                    token["liquidity"]
                )
            )

    return entries, exits


# ============================================================
# 12. FULL SCAN
# ============================================================

def run_scan(
    cause,
    enabled,
    settings
):
    stamp = now()

    discovered, errors = discovery()

    market, successful, problems = prices(
        list(discovered)
    )

    errors.extend(problems)

    observations = []

    open_addresses = {
        row[0]
        for row in query(
            """
            SELECT address
            FROM paper_v54
            WHERE status='OPEN'
            """
        )
    }

    for address, pair in market.items():

        try:
            token = assess(
                address,
                pair,
                discovered[address],
                stamp
            )

            if (
                token["narrative"] >= 65
                or address in open_addresses
            ):
                observations.append(token)

        except (
            ValueError,
            TypeError,
            KeyError
        ) as error:

            errors.append(
                f"Analyse {address[:8]}: {error}"
            )

    missing = [
        address
        for address in successful - set(market)
        if query(
            """
            SELECT 1
            FROM tracking_state
            WHERE address=?
            """,
            (address,)
        )
    ]

    save_observations(
        observations,
        missing,
        stamp
    )

    entries, exits = paper_engine(
        observations,
        stamp,
        enabled,
        settings
    )

    finished = now()

    with conn() as connection:

        connection.execute(
            """
            INSERT OR REPLACE INTO v541_runs(
                scanned_at,
                finished_at,
                fetched,
                errors,
                entries,
                exits,
                cause
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                stamp,
                finished,
                len(observations),
                len(errors),
                entries,
                exits,
                cause
            )
        )

    return (
        pd.DataFrame(observations),
        errors,
        stamp,
        entries,
        exits
    )


# ============================================================
# 13. LOAD LAST SCAN
# ============================================================

def recent_frame():

    latest = query(
        """
        SELECT scanned_at
        FROM v541_runs
        ORDER BY scanned_at DESC
        LIMIT 1
        """
    )

    if not latest:
        return pd.DataFrame()

    data = query(
        """
        SELECT
            address,
            name,
            symbol,
            source,
            narrative,
            age_hours,
            price,
            liquidity,
            volume_m5,
            buys_m5,
            sells_m5,
            momentum,
            evidence,
            phase,
            warnings,
            pair_address
        FROM radar_v54
        WHERE scanned_at=?
        """,
        (latest[0][0],)
    )

    columns = [
        "address",
        "name",
        "symbol",
        "sources",
        "narrative",
        "age",
        "price",
        "liquidity",
        "volume",
        "buys",
        "sells",
        "momentum",
        "evidence",
        "phase",
        "warnings",
        "pair"
    ]

    return pd.DataFrame(
        data,
        columns=columns
    )


# ============================================================
# 14. START APPLICATION
# ============================================================

setup()

st.title("🎮 GTA 6 Narrative Radar V5.4.1")

st.caption(
    "Persistent Tracking | Momentum | "
    "Automatische PAPER trading | Belgische tijd"
)

st.warning(
    "Dit is een onderzoeksprototype. "
    "Er worden geen echte orders uitgevoerd. "
    "Tokencontracten, houders en verkoopbaarheid "
    "zijn niet volledig gecontroleerd."
)


# ============================================================
# 15. SIDEBAR SETTINGS
# ============================================================

st.sidebar.header("⚙️ Scannerinstellingen")

if "v541_auto" not in st.session_state:

    st.session_state.v541_auto = config_get(
        "auto_scan",
        True
    )

if "v541_paper" not in st.session_state:

    st.session_state.v541_paper = config_get(
        "paper",
        False
    )


def update_auto():
    config_set(
        "auto_scan",
        st.session_state.v541_auto
    )


def update_paper():
    config_set(
        "paper",
        st.session_state.v541_paper
    )


st.sidebar.toggle(
    "Automatisch scannen (actieve sessie)",
    key="v541_auto",
    on_change=update_auto
)

st.sidebar.toggle(
    "Automatische PAPER trading",
    key="v541_paper",
    on_change=update_paper
)

interval = st.sidebar.selectbox(
    "Scaninterval (minuten)",
    [5, 10, 15],
    index=0
)

minimum = st.sidebar.number_input(
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
    "Take profit (%)",
    min_value=5,
    max_value=500,
    value=30,
    step=5
)

stop = st.sidebar.number_input(
    "Stop loss (%)",
    min_value=5,
    max_value=95,
    value=15,
    step=5
)

hours = st.sidebar.number_input(
    "Maximale looptijd (uren)",
    min_value=1,
    max_value=72,
    value=4,
    step=1
)

settings = {
    "minimum": minimum,
    "amount": amount,
    "fee": fee / 100,
    "slip": slip / 100,
    "take": take / 100,
    "stop": stop / 100,
    "hours": float(hours)
}

st.sidebar.caption(
    "Automatische scans vereisen een actieve "
    "Streamlit-sessie. Community Cloud is niet "
    "geschikt als gegarandeerde 24/7-scanner."
)

if "v541_scan_errors" not in st.session_state:
    st.session_state.v541_scan_errors = []


# ============================================================
# 16. LIVE DASHBOARD
# ============================================================

@st.fragment(run_every="60s")
def dashboard():

    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    latest = query(
        """
        SELECT
            scanned_at,
            finished_at,
            cause
        FROM v541_runs
        ORDER BY scanned_at DESC
        LIMIT 1
        """
    )

    if latest:
        last_scan = latest[0]

        finished = dt(
            last_scan[1] or last_scan[0]
        )

    else:
        last_scan = None
        finished = None

    current = datetime.now(
        timezone.utc
    )

    if finished:
        next_due = (
            finished
            + timedelta(minutes=interval)
        )
    else:
        next_due = current

    due = (
        st.session_state.v541_auto
        and current >= next_due
    )

    # --------------------------------------------------------
    # SCAN PLANNING: BELGIAN TIME
    # --------------------------------------------------------

    st.subheader("🕒 Scanplanning")

    status_auto = (
        "AAN"
        if st.session_state.v541_auto
        else "UIT"
    )

    status_paper = (
        "AAN"
        if st.session_state.v541_paper
        else "UIT"
    )

    st.write(
        f"**Automatisch scannen:** {status_auto}"
    )

    st.write(
        f"**Automatische PAPER trading:** {status_paper}"
    )

    if last_scan:

        st.write(
            "**Laatste voltooide scan:** "
            f"{belgian_time(last_scan[1] or last_scan[0])} "
            f"(Belgische tijd, {last_scan[2]})"
        )

    else:
        st.write(
            "**Laatste scan:** nog geen scan uitgevoerd"
        )

    if st.session_state.v541_auto:

        st.write(
            "**Volgende scan vanaf:** "
            f"{belgian_time(next_due.isoformat())} "
            "(Belgische tijd)"
        )

        seconds_remaining = max(
            0,
            int(
                (next_due - current)
                .total_seconds()
            )
        )

        st.caption(
            "Nog ongeveer "
            f"{seconds_remaining // 60} min "
            f"{seconds_remaining % 60} sec "
            "tot de volgende geplande controle."
        )

    else:
        st.info(
            "Automatisch scannen staat uit."
        )

    st.caption(
        "De planning is indicatief: de fragmentcontrole "
        "loopt ongeveer eenmaal per minuut zolang de "
        "sessie actief is. API-vertragingen of een slapende "
        "Streamlit-server kunnen scans uitstellen."
    )

    # --------------------------------------------------------
    # RUN SCAN
    # --------------------------------------------------------

    if manual or due:

        cause = (
            "MANUEEL"
            if manual
            else "AUTOMATISCH"
        )

        with st.spinner(
            "Tokens ontdekken en marktgegevens ophalen..."
        ):

            try:
                (
                    data,
                    errors,
                    timestamp,
                    entries,
                    exits
                ) = run_scan(
                    cause,
                    st.session_state.v541_paper,
                    settings
                )

                st.session_state.v541_scan_errors = (
                    errors
                )

                st.success(
                    f"Scan voltooid om "
                    f"{belgian_time(timestamp)}. "
                    f"{len(data)} tokens, "
                    f"{entries} virtuele instappen en "
                    f"{exits} virtuele uitstappen."
                )

            except (
                requests.RequestException,
                sqlite3.Error,
                ValueError,
                TypeError
            ) as error:

                st.error(
                    f"Scanfout: {error}"
                )

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    df = recent_frame()

    total_discovered = query(
        """
        SELECT COUNT(*)
        FROM discovered
        """
    )[0][0]

    open_positions = query(
        """
        SELECT COUNT(*)
        FROM paper_v54
        WHERE status='OPEN'
        """
    )[0][0]

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Tokens laatste scan",
        len(df)
    )

    c2.metric(
        "Momentum 80",
        int(
            (df["momentum"] == 80).sum()
        ) if not df.empty else 0
    )

    c3.metric(
        "Historisch ontdekt",
        total_discovered
    )

    c4.metric(
        "Open paper trades",
        open_positions
    )

    # --------------------------------------------------------
    # TOKEN TABLE
    # --------------------------------------------------------

    if not df.empty:

        st.subheader(
            "🚀 Nieuwe GTA-tokens"
        )

        early = df[
            df["age"].notna()
            & (df["age"] < 24)
        ]

        st.dataframe(
            early.sort_values("age"),
            hide_index=True,
            use_container_width=True
        )

        st.subheader(
            "📈 GTA Momentum Radar"
        )

        st.dataframe(
            df.sort_values(
                [
                    "momentum",
                    "volume"
                ],
                ascending=False
            ),
            hide_index=True,
            use_container_width=True
        )

        # ----------------------------------------------------
        # TOKEN DETAILS
        # ----------------------------------------------------

        options = {
            (
                f"{row['name']} | "
                f"{row['address'][:10]}"
            ): row["address"]

            for _, row in df.iterrows()
        }

        selected_label = st.selectbox(
            "Token bekijken",
            list(options)
        )

        selected_address = options[
            selected_label
        ]

        selected = df[
            df["address"] == selected_address
        ].iloc[0]

        st.code(
            selected_address,
            language="text"
        )

        st.write(
            "**Trend:**",
            selected["evidence"]
        )

        st.write(
            "**Risico:**",
            selected["warnings"]
        )

        pair = (
            selected["pair"]
            or selected_address
        )

        st.link_button(
            "Bekijk op DexScreener",
            "https://dexscreener.com/solana/"
            + pair
        )

        if st.button(
            "⭐ Voeg toe aan watchlist"
        ):

            with conn() as connection:

                connection.execute(
                    """
                    INSERT OR IGNORE INTO watchlist
                    (address,name,added_at)
                    VALUES(?,?,?)
                    """,
                    (
                        selected_address,
                        selected["name"],
                        now()
                    )
                )

            st.success(
                "Toegevoegd aan watchlist"
            )

        # ----------------------------------------------------
        # TOKEN HISTORY
        # ----------------------------------------------------

        history = query(
            """
            SELECT
                scanned_at,
                price,
                volume_m5
            FROM radar_v54
            WHERE address=?
            ORDER BY scanned_at DESC
            LIMIT 100
            """,
            (selected_address,)
        )

        if len(history) >= 2:

            history_df = pd.DataFrame(
                history,
                columns=[
                    "time",
                    "price",
                    "volume"
                ]
            )

            history_df["time"] = (
                pd.to_datetime(
                    history_df["time"],
                    utc=True
                ).dt.tz_convert(
                    "Europe/Brussels"
                )
            )

            history_df = (
                history_df
                .sort_values("time")
                .set_index("time")
            )

            st.subheader(
                "Prijsontwikkeling"
            )

            st.line_chart(
                history_df[["price"]]
            )

            st.subheader(
                "5-minutenvolume"
            )

            st.line_chart(
                history_df[["volume"]]
            )

        st.download_button(
            "📥 Download scan CSV",
            df.to_csv(
                index=False
            ).encode("utf-8"),
            "gta6_radar_v541.csv",
            mime="text/csv"
        )

    else:
        st.info(
            "Er zijn nog geen V5.4.1-scanresultaten."
        )

    # --------------------------------------------------------
    # PAPER TRADING DECISION LOG
    # --------------------------------------------------------

    st.subheader(
        "🔎 Waarom opent de paperbot geen positie?"
    )

    latest_run = query(
        """
        SELECT scanned_at
        FROM v541_runs
        ORDER BY scanned_at DESC
        LIMIT 1
        """
    )

    if latest_run:

        decisions = query(
            """
            SELECT
                name,
                address,
                outcome,
                reason,
                price,
                liquidity
            FROM v541_decisions
            WHERE scan_at=?
            ORDER BY
                CASE outcome
                    WHEN 'PAPER_ENTRY' THEN 0
                    WHEN 'KANDIDAAT' THEN 1
                    WHEN 'UITGESCHAKELD' THEN 2
                    WHEN 'GEBLOKKEERD' THEN 3
                    ELSE 4
                END,
                name
            LIMIT 400
            """,
            (latest_run[0][0],)
        )

        decision_df = pd.DataFrame(
            decisions,
            columns=[
                "Token",
                "Adres",
                "Besluit",
                "Reden",
                "Prijs",
                "Liquiditeit"
            ]
        )

        st.dataframe(
            decision_df,
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            "📥 Download beslissingslog",
            decision_df.to_csv(
                index=False
            ).encode("utf-8"),
            "gta6_beslissingen.csv",
            mime="text/csv"
        )

    # --------------------------------------------------------
    # PAPER TRADING
    # --------------------------------------------------------

    st.subheader(
        "🧪 Paper Trading Portfolio"
    )

    trades = query(
        """
        SELECT
            id,
            name,
            address,
            opened_at,
            amount_usd,
            entry_market,
            last_mark,
            status,
            pnl_usd,
            exit_reason
        FROM paper_v54
        ORDER BY id DESC
        LIMIT 200
        """
    )

    if trades:

        trades_df = pd.DataFrame(
            trades,
            columns=[
                "ID",
                "Naam",
                "Adres",
                "Geopend UTC",
                "Inzet",
                "Instap",
                "Laatste prijs",
                "Status",
                "Resultaat",
                "Exitreden"
            ]
        )

        trades_df["Geopend België"] = (
            pd.to_datetime(
                trades_df["Geopend UTC"],
                utc=True
            )
            .dt.tz_convert(
                "Europe/Brussels"
            )
            .dt.strftime(
                "%d-%m-%Y %H:%M:%S"
            )
        )

        trades_df = trades_df.drop(
            columns=["Geopend UTC"]
        )

        st.dataframe(
            trades_df,
            hide_index=True,
            use_container_width=True
        )

        closed = trades_df[
            trades_df["Status"] == "CLOSED"
        ]

        if not closed.empty:

            p1, p2 = st.columns(2)

            p1.metric(
                "Gerealiseerd virtueel resultaat",
                f"${closed['Resultaat'].sum():,.2f}"
            )

            win_rate = (
                (closed["Resultaat"] > 0)
                .mean() * 100
            )

            p2.metric(
                "Winnende trades",
                f"{win_rate:.1f}%"
            )

    else:
        st.info(
            "Nog geen virtuele posities geregistreerd. "
            "Bekijk het beslissingslog om te zien "
            "welke voorwaarden nog ontbreken."
        )

    # --------------------------------------------------------
    # SCAN LOG WITH BELGIAN TIME
    # --------------------------------------------------------

    st.subheader(
        "🕒 Scanlogboek"
    )

    logs = query(
        """
        SELECT
            scanned_at,
            finished_at,
            fetched,
            errors,
            entries,
            exits,
            cause
        FROM v541_runs
        ORDER BY scanned_at DESC
        LIMIT 30
        """
    )

    if logs:

        log_df = pd.DataFrame(
            logs,
            columns=[
                "Start UTC",
                "Einde UTC",
                "Tokens",
                "API-fouten",
                "Instappen",
                "Uitstappen",
                "Trigger"
            ]
        )

        log_df["Start Belgische tijd"] = (
            pd.to_datetime(
                log_df["Start UTC"],
                utc=True
            )
            .dt.tz_convert(
                "Europe/Brussels"
            )
            .dt.strftime(
                "%d-%m-%Y %H:%M:%S"
            )
        )

        log_df["Einde Belgische tijd"] = (
            pd.to_datetime(
                log_df["Einde UTC"],
                utc=True
            )
            .dt.tz_convert(
                "Europe/Brussels"
            )
            .dt.strftime(
                "%d-%m-%Y %H:%M:%S"
            )
        )

        log_df = log_df.drop(
            columns=[
                "Start UTC",
                "Einde UTC"
            ]
        )

        st.dataframe(
            log_df,
            hide_index=True,
            use_container_width=True
        )

    # --------------------------------------------------------
    # ERROR LOG
    # --------------------------------------------------------

    if st.session_state.v541_scan_errors:

        with st.expander(
            "⚠️ API-fouten"
        ):
            st.write(
                st.session_state.v541_scan_errors[:30]
            )


# ============================================================
# 17. RUN DASHBOARD
# ============================================================

dashboard()

st.caption(
    "GTA 6 Radar V5.4.1 | "
    "Dashboardtijd: Europe/Brussels | "
    "Database: UTC | "
    "Uitsluitend virtuele handel."
)
