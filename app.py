
"""GTA 6 Token Radar V5.2 — read-only signal research, no trading."""
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GTA 6 Radar V5.2",
    page_icon="🎮",
    layout="wide"
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2"

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
    "raul bautista", "boobie ike", "ambrosia"
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GTA6-Token-Radar/5.2"
})


# ==================================================
# HELPERS
# ==================================================

def now():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def val(value):
    try:
        return float(value) if value is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def contains(text, words):
    text = str(text or "").lower()

    return any(
        re.search(
            r"(?<![a-z0-9])"
            + re.escape(w)
            + r"(?![a-z0-9])",
            text
        )
        for w in words
    )


# ==================================================
# DATABASE
# ==================================================

def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def read(sql, args=()):
    with connect() as con:
        return con.execute(sql, args).fetchall()


def initialize():
    # Existing V4/V5/V5.1 tables are preserved.
    schemas = [
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            address TEXT,
            scanned_at TEXT,
            price REAL,
            liquidity REAL,
            volume24 REAL,
            buys24 INTEGER,
            sells24 INTEGER
        )
        """,
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
        CREATE TABLE IF NOT EXISTS wallet_snapshots (
            address TEXT,
            scanned_at TEXT,
            top10_percent REAL,
            top20_percent REAL,
            sampled_accounts INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_events (
            address TEXT PRIMARY KEY,
            detected_at TEXT,
            name TEXT,
            symbol TEXT,
            pair_age_hours REAL,
            source TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS momentum_snapshots (
            address TEXT,
            scanned_at TEXT,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            liquidity REAL,
            price REAL,
            PRIMARY KEY(address, scanned_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS token_signals (
            address TEXT,
            scanned_at TEXT,
            signal TEXT,
            detail TEXT,
            PRIMARY KEY(address, scanned_at, signal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            scanned_at TEXT PRIMARY KEY,
            candidates INTEGER,
            errors INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS radar_v52 (
            address TEXT,
            scanned_at TEXT,
            name TEXT,
            symbol TEXT,
            source TEXT,
            pair_age_hours REAL,
            price REAL,
            liquidity REAL,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            narrative INTEGER,
            discovery INTEGER,
            momentum INTEGER,
            risk TEXT,
            phase TEXT,
            evidence TEXT,
            PRIMARY KEY(address, scanned_at)
        )
        """
    ]

    with connect() as con:
        for sql in schemas:
            con.execute(sql)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_v52_history
            ON radar_v52(address, scanned_at)
        """)


# ==================================================
# API
# ==================================================

def api(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=18
    )
    response.raise_for_status()
    return response.json()


# ==================================================
# GTA NARRATIVE DETECTION
# ==================================================

def narrative(name, ticker, text):
    title = f"{name} {ticker}"

    if contains(title, STRONG):
        return 100, "Direct GTA 6"

    if contains(title, RELATED):
        return 65, "GTA-gerelateerde naam"

    if contains(text, STRONG):
        return 75, "GTA in profiel"

    if contains(text, RELATED):
        return 35, "Zwakke GTA-context"

    return 0, "Geen match"


# ==================================================
# TOKEN DISCOVERY
# ==================================================

def discover():
    candidates = {}
    issues = []

    def add(address, source, extra=""):
        if not address:
            return

        if not re.fullmatch(
            r"[1-9A-HJ-NP-Za-km-z]{32,44}",
            address
        ):
            return

        if len(candidates) >= 200 and address not in candidates:
            return

        obj = candidates.setdefault(
            address,
            {"sources": set(), "text": ""}
        )

        obj["sources"].add(source)
        obj["text"] = (
            obj["text"] + " " + str(extra)
        )[:3000]

    # DexScreener keyword search
    for term in TERMS:
        try:
            response = api(
                DEX + "/latest/dex/search",
                {"q": term}
            )

            for pair in response.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                token = pair.get("baseToken") or {}

                add(
                    token.get("address"),
                    "DEX search",
                    f"{token.get('name', '')} "
                    f"{token.get('symbol', '')}"
                )

        except (requests.RequestException, ValueError) as exc:
            issues.append(
                f"DEX search ({term}): {exc}"
            )

    # Latest profiles and boosted tokens
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]

    for endpoint in endpoints:
        try:
            data = api(DEX + endpoint)

            for item in data:
                if item.get("chainId") != "solana":
                    continue

                links = " ".join(
                    str(link.get("url", ""))
                    for link in item.get("links") or []
                )

                add(
                    item.get("tokenAddress"),
                    "DEX profiles",
                    str(item.get("description") or "")
                    + " " + links
                )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            issues.append(
                f"DEX profiles ({endpoint}): {exc}"
            )

    # GeckoTerminal newly created pools
    try:
        response = api(
            GECKO + "/networks/solana/new_pools",
            {
                "include": "base_token,quote_token",
                "page": 1
            }
        )

        included = {
            obj.get("id"): obj.get("attributes") or {}
            for obj in response.get("included") or []
        }

        for pool in response.get("data") or []:
            relation = (
                (pool.get("relationships") or {})
                .get("base_token") or {}
            )

            token_id = (
                (relation.get("data") or {})
                .get("id", "")
            )

            token = included.get(token_id, {})

            address = token.get("address") or (
                token_id.split("_", 1)[1]
                if "_" in token_id
                else ""
            )

            label = (
                f"{token.get('name', '')} "
                f"{token.get('symbol', '')} "
                f"{(pool.get('attributes') or {}).get('name', '')}"
            )

            if contains(label, STRONG + RELATED):
                add(
                    address,
                    "Gecko new pools",
                    label
                )

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ) as exc:
        issues.append(f"Gecko: {exc}")

    # Always include watchlist
    for address, name in read(
        "SELECT address, name FROM watchlist"
    ):
        add(address, "Watchlist", name)

    return candidates, issues


# ==================================================
# MARKET DATA
# ==================================================

def fetch_pairs(addresses):
    selected = {}
    issues = []
    keys = list(addresses)

    for start in range(0, len(keys), 30):
        chunk = keys[start:start + 30]

        try:
            pairs = api(
                DEX + "/tokens/v1/solana/"
                + ",".join(chunk)
            )

            if not isinstance(pairs, list):
                continue

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address", "")

                if address not in addresses:
                    continue

                liquidity = val(
                    (pair.get("liquidity") or {})
                    .get("usd")
                )

                previous = val(
                    (
                        selected.get(address, {})
                        .get("liquidity") or {}
                    ).get("usd")
                )

                if (
                    address not in selected
                    or liquidity > previous
                ):
                    selected[address] = pair

        except (
            requests.RequestException,
            ValueError
        ) as exc:
            issues.append(
                f"Pairs batch {start // 30 + 1}: {exc}"
            )

    return selected, issues


def prior_samples(address):
    return read(
        """
        SELECT scanned_at, price, volume_m5,
               buys_m5, sells_m5
        FROM radar_v52
        WHERE address=?
        ORDER BY scanned_at DESC
        LIMIT 3
        """,
        (address,)
    )


# ==================================================
# TREND INTELLIGENCE
# ==================================================

def analyze(address, pair, metadata, stamp):
    token = pair.get("baseToken") or {}

    name = token.get("name") or "Unknown"
    ticker = token.get("symbol") or "?"

    info = pair.get("info") or {}

    links = (
        (info.get("websites") or [])
        + (info.get("socials") or [])
    )

    extra = metadata["text"] + " " + " ".join(
        str(item.get("url", ""))
        for item in links
    )

    nscore, reason = narrative(
        name, ticker, extra
    )

    if not nscore:
        return None

    creation = val(pair.get("pairCreatedAt"))

    age = (
        max(
            0,
            (
                datetime.fromisoformat(stamp).timestamp()
                * 1000 - creation
            ) / 3600000
        )
        if creation else None
    )

    price = val(pair.get("priceUsd"))

    liquid = val(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume5 = val(
        (pair.get("volume") or {}).get("m5")
    )

    volume24 = val(
        (pair.get("volume") or {}).get("h24")
    )

    transactions5 = (
        (pair.get("txns") or {}).get("m5") or {}
    )

    transactions24 = (
        (pair.get("txns") or {}).get("h24") or {}
    )

    buys = int(val(transactions5.get("buys")))
    sells = int(val(transactions5.get("sells")))

    buys24 = int(val(transactions24.get("buys")))
    sells24 = int(val(transactions24.get("sells")))

    older = prior_samples(address)

    # Early discovery score
    if age is None:
        discovery = 10
    elif age < 1:
        discovery = 90
    elif age < 6:
        discovery = 75
    elif age < 24:
        discovery = 60
    elif age < 72:
        discovery = 35
    else:
        discovery = 10

    # Momentum confirmation
    evidence = []
    momentum = 0
    volume_growth = False
    price_growth = False
    confirmed = False

    if len(older) >= 2:
        recent = older[0]
        earlier = older[1]

        gap1 = (
            datetime.fromisoformat(stamp)
            - datetime.fromisoformat(recent[0])
        ).total_seconds() / 60

        gap2 = (
            datetime.fromisoformat(recent[0])
            - datetime.fromisoformat(earlier[0])
        ).total_seconds() / 60

        if (
            4 <= gap1 <= 12
            and 4 <= gap2 <= 12
        ):
            confirmed = True

            v1 = val(earlier[2])
            v2 = val(recent[2])

            p1 = val(earlier[1])
            p2 = val(recent[1])

            volume_growth = (
                v1 >= 50
                and v1 < v2 < volume5
                and volume5 >= 250
            )

            price_growth = (
                p1 > 0
                and p1 < p2 < price
                and price >= p1 * 1.02
            )

            if volume_growth:
                momentum += 35
                evidence.append(
                    "3 stijgende volumemetingen"
                )

            if price_growth:
                momentum += 35
                evidence.append(
                    "3 stijgende prijsmetingen"
                )

    # Current market activity
    if buys + sells >= 20 and volume5 >= 250:
        momentum += 15
        evidence.append(
            "voldoende 5m-activiteit"
        )

    if (
        buys >= 10
        and buys >= 1.4 * max(sells, 1)
    ):
        momentum += 15
        evidence.append(
            "meer koop- dan verkooptransacties"
        )

    momentum = min(100, momentum)

    # Risk screening
    risk_flags = []

    if liquid < 1000:
        risk_flags.append(
            "extreem lage liquiditeit"
        )
    elif liquid < 10000:
        risk_flags.append(
            "lage liquiditeit"
        )

    if (
        buys + sells > 300
        and volume5 / max(buys + sells, 1) < 3
    ):
        risk_flags.append(
            "zeer kleine gemiddelde transactie"
        )

    if age is None:
        risk_flags.append(
            "paarleeftijd onbekend"
        )

    risk_flags.append(
        "holders/contractbevoegdheden/"
        "verkoopbaarheid niet gecontroleerd"
    )

    risk = (
        "Hoog"
        if liquid < 10000
        else "Onbekend"
    )

    # Signal stage
    if (
        confirmed
        and volume_growth
        and price_growth
        and liquid >= 10000
        and momentum >= 70
    ):
        stage = "MOMENTUM TER CONTROLE"

    elif age is not None and age <= 24:
        stage = "PRE-TREND KANDIDAAT"

    elif volume5 >= 250 and buys + sells >= 20:
        stage = "ACTIEF — MONITOREN"

    else:
        stage = "MONITOREN"

    return {
        "Naam": name,
        "Ticker": ticker,
        "Adres": address,
        "Bron": ", ".join(
            sorted(metadata["sources"])
        ),
        "Narrative": nscore,
        "Waarom": reason,
        "Early Discovery": discovery,
        "Momentum": momentum,
        "Fase": stage,
        "Risico": risk,
        "Waarschuwingen": "; ".join(risk_flags),
        "Leeftijd uren": (
            round(age, 2)
            if age is not None else None
        ),
        "Liquiditeit USD": liquid,
        "Volume 5m USD": volume5,
        "Volume 24u USD": volume24,
        "Koop 5m": buys,
        "Verkoop 5m": sells,
        "Koop 24u": buys24,
        "Verkoop 24u": sells24,
        "Prijs USD": price,
        "Bewijs": (
            "; ".join(evidence)
            if evidence
            else "Nog onvoldoende trendbewijs"
        ),
        "DEX": pair.get("url") or ""
    }


# ==================================================
# DATABASE STORAGE
# ==================================================

def save(rows, stamp, error_count):
    with connect() as con:
        for r in rows:
            a = r["Adres"]

            con.execute(
                """
                INSERT INTO discovered
                (address, name, symbol,
                 first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
                """,
                (
                    a, r["Naam"], r["Ticker"],
                    stamp, stamp
                )
            )

            con.execute(
                """
                INSERT OR IGNORE INTO discovery_events
                (address, detected_at, name,
                 symbol, pair_age_hours, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    a, stamp, r["Naam"],
                    r["Ticker"],
                    r["Leeftijd uren"], r["Bron"]
                )
            )

            con.execute(
                """
                INSERT INTO snapshots
                (address, scanned_at, price,
                 liquidity, volume24, buys24, sells24)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a, stamp, r["Prijs USD"],
                    r["Liquiditeit USD"],
                    r["Volume 24u USD"],
                    r["Koop 24u"],
                    r["Verkoop 24u"]
                )
            )

            con.execute(
                """
                INSERT OR IGNORE INTO momentum_snapshots
                (address, scanned_at, volume_m5,
                 buys_m5, sells_m5, liquidity, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a, stamp, r["Volume 5m USD"],
                    r["Koop 5m"],
                    r["Verkoop 5m"],
                    r["Liquiditeit USD"],
                    r["Prijs USD"]
                )
            )

            con.execute(
                """
                INSERT OR REPLACE INTO radar_v52
                (address, scanned_at, name, symbol,
                 source, pair_age_hours, price,
                 liquidity, volume_m5, buys_m5,
                 sells_m5, narrative, discovery,
                 momentum, risk, phase, evidence)
                VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    a, stamp,
                    r["Naam"], r["Ticker"],
                    r["Bron"], r["Leeftijd uren"],
                    r["Prijs USD"],
                    r["Liquiditeit USD"],
                    r["Volume 5m USD"],
                    r["Koop 5m"],
                    r["Verkoop 5m"],
                    r["Narrative"],
                    r["Early Discovery"],
                    r["Momentum"],
                    r["Risico"],
                    r["Fase"],
                    r["Bewijs"]
                )
            )

            if r["Fase"] == "MOMENTUM TER CONTROLE":
                con.execute(
                    """
                    INSERT OR IGNORE INTO token_signals
                    (address, scanned_at, signal, detail)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        a, stamp,
                        "V52_TREND",
                        r["Bewijs"]
                    )
                )

        con.execute(
            """
            INSERT OR REPLACE INTO scan_runs
            (scanned_at, candidates, errors)
            VALUES (?, ?, ?)
            """,
            (
                stamp,
                len(rows),
                error_count
            )
        )


# ==================================================
# SCAN ENGINE
# ==================================================

def perform_scan():
    stamp = now()

    candidates, errors = discover()

    pairs, pair_errors = fetch_pairs(candidates)
    errors.extend(pair_errors)

    rows = []

    for address, pair in pairs.items():
        try:
            result = analyze(
                address,
                pair,
                candidates[address],
                stamp
            )

            if result:
                rows.append(result)

        except (
            KeyError,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f"Analyse {address[:10]}: {exc}"
            )

    save(rows, stamp, len(errors))

    return pd.DataFrame(rows), errors, stamp


# ==================================================
# STREAMLIT INTERFACE
# ==================================================

initialize()

st.title("🎮 GTA 6 Narrative Trading Radar V5.2")

st.caption(
    "Solana • Early Discovery • "
    "Trendbevestiging • Geen echte transacties"
)

st.warning(
    "De scanner kan fake volume, wash trading, "
    "scams of verkoopbeperkingen niet uitsluiten. "
    "Een signaal is geen koopadvies."
)

auto = st.sidebar.toggle(
    "Automatische scan bij actieve sessie (5 min)",
    value=False
)

max_age = st.sidebar.slider(
    "Maximale leeftijd pre-trend (uren)",
    1, 72, 24
)

minimum_liquidity = st.sidebar.number_input(
    "Minimum liquiditeit shortlist ($)",
    min_value=0,
    value=10000,
    step=1000
)

st.sidebar.caption(
    "Community Cloud kan in slaapstand gaan. "
    "SQLite is daar niet gegarandeerd permanent."
)

if "v52_data" not in st.session_state:
    st.session_state.v52_data = pd.DataFrame()
    st.session_state.v52_errors = []
    st.session_state.v52_last = "Nog niet"
    st.session_state.v52_last_attempt = 0.0


@st.fragment(run_every="300s")
def dashboard():
    clicked = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    due = (
        auto
        and time.monotonic()
        - st.session_state.v52_last_attempt >= 300
    )

    if clicked or due:
        st.session_state.v52_last_attempt = (
            time.monotonic()
        )

        with st.spinner(
            "Scannen en signalen vergelijken..."
        ):
            try:
                data, errors, timestamp = perform_scan()

                st.session_state.v52_data = data
                st.session_state.v52_errors = errors
                st.session_state.v52_last = timestamp

            except (
                requests.RequestException,
                sqlite3.Error,
                ValueError
            ) as exc:
                st.error(
                    f"Scan mislukt: {exc}"
                )

    df = st.session_state.v52_data

    st.caption(
        "Laatste scan (UTC): "
        f"{st.session_state.v52_last}"
    )

    if df.empty:
        st.info(
            "Nog geen resultaten. Klik op Scan nu."
        )
        return

    first, second, third = st.columns(3)

    first.metric(
        "GTA-kandidaten",
        len(df)
    )

    second.metric(
        "Paar jonger dan 24 uur",
        int(
            (
                df["Leeftijd uren"].fillna(1e9) < 24
            ).sum()
        )
    )

    third.metric(
        "Trendbevestigingen",
        int(
            (
                df["Fase"] == "MOMENTUM TER CONTROLE"
            ).sum()
        )
    )

    st.subheader(
        "🚀 Pre-trend — nieuwe paren"
    )

    young = df[
        (df["Leeftijd uren"].notna())
        & (df["Leeftijd uren"] <= max_age)
    ].sort_values("Leeftijd uren")

    st.dataframe(
        young,
        hide_index=True,
        use_container_width=True
    )

    st.subheader(
        "📈 Momentum & liquiditeit"
    )

    active = df[
        df["Liquiditeit USD"]
        >= minimum_liquidity
    ].sort_values(
        ["Momentum", "Early Discovery"],
        ascending=False
    )

    st.dataframe(
        active,
        hide_index=True,
        use_container_width=True
    )

    with st.expander("Alle GTA-kandidaten"):
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True
        )

    options = {
        (
            f"{r['Naam']} ({r['Ticker']}) "
            f"— {r['Adres'][:9]}"
        ): r["Adres"]
        for _, r in df.iterrows()
    }

    label = st.selectbox(
        "🔎 Token kiezen",
        list(options)
    )

    address = options[label]

    selected = df[
        df["Adres"] == address
    ].iloc[0]

    st.code(
        address,
        language="text"
    )

    st.write(
        "**Trendbewijs:**",
        selected["Bewijs"]
    )

    st.write(
        "**Risico's:**",
        selected["Waarschuwingen"]
    )

    if st.button(
        "➕ Aan watchlist toevoegen"
    ):
        with connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO watchlist
                (address, name, added_at)
                VALUES (?, ?, ?)
                """,
                (
                    address,
                    selected["Naam"],
                    now()
                )
            )

        st.success("Toegevoegd")

    if selected["DEX"]:
        st.link_button(
            "Open DexScreener",
            selected["DEX"]
        )

    history = read(
        """
        SELECT scanned_at, price,
               volume_m5, liquidity
        FROM radar_v52
        WHERE address=?
        ORDER BY scanned_at DESC
        LIMIT 100
        """,
        (address,)
    )

    if len(history) >= 2:
        chart = pd.DataFrame(
            history,
            columns=[
                "Tijd", "Prijs",
                "Volume 5m", "Liquiditeit"
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
            chart[["Volume 5m"]]
        )

    st.download_button(
        "📥 CSV-export",
        df.to_csv(index=False).encode("utf-8"),
        "gta6_v52.csv",
        "text/csv"
    )

    if st.session_state.v52_errors:
        with st.expander(
            "⚠️ API-meldingen"
        ):
            st.write(
                st.session_state.v52_errors[:25]
            )


dashboard()

st.divider()

# ==================================================
# WATCHLIST AND SIGNAL HISTORY
# ==================================================

st.subheader("⭐ Watchlist")

watch = read(
    """
    SELECT name, address, added_at
    FROM watchlist
    ORDER BY added_at DESC
    LIMIT 100
    """
)

if watch:
    st.dataframe(
        pd.DataFrame(
            watch,
            columns=[
                "Naam", "Adres", "Toegevoegd"
            ]
        ),
        hide_index=True,
        use_container_width=True
    )

st.subheader("📣 Trendmeldingen")

signals = read(
    """
    SELECT scanned_at, address,
           signal, detail
    FROM token_signals
    WHERE signal='V52_TREND'
    ORDER BY scanned_at DESC
    LIMIT 50
    """
)

if signals:
    st.dataframe(
        pd.DataFrame(
            signals,
            columns=[
                "Tijd", "Adres",
                "Type", "Bewijs"
            ]
        ),
        hide_index=True,
        use_container_width=True
    )

st.caption(
    "Geen handelsorders. "
    "Geen uniek-wallet- of buy/sell-volumeanalyse "
    "zonder aanvullende on-chain databron."
)
