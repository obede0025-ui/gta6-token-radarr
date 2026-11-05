
"""GTA 6 Narrative Radar v5.1 — research and signal tracking, not live trading."""
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GTA 6 Radar V5.1",
    page_icon="🎮",
    layout="wide"
)

DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2"
DB = os.environ.get("GTA6_RADAR_DB", "gta6_radar.db")

SEARCH_TERMS = [
    "GTA6", "GTA VI", "Grand Theft Auto",
    "Vice City", "Leonida", "Port Gellhorn",
    "Lucia Camino", "Jason Duval"
]

STRONG = [
    "gta6", "gta 6", "gta vi",
    "grand theft auto", "grandtheftauto"
]

RELATED = [
    "vice city", "leonida", "port gellhorn",
    "lucia camino", "jason duval", "ambrosia",
    "raul bautista", "boobie ike", "mount kalaga"
]

MAX_CANDIDATES = 150

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GTA6-Narrative-Radar/5.1",
    "Accept": "application/json"
})


# ==========================================================
# HELPERS
# ==========================================================

def now():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def num(x):
    try:
        return float(x) if x is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def has_term(text, terms):
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


# ==========================================================
# DATABASE — BESTAANDE GEGEVENS BEHOUDEN
# ==========================================================

def db_connect():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def db_all(query, params=()):
    with db_connect() as con:
        return con.execute(query, params).fetchall()


def init_db():
    schema = [
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
        CREATE TABLE IF NOT EXISTS radar_v51 (
            address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT,
            symbol TEXT,
            source TEXT,
            pair_created_at INTEGER,
            age_hours REAL,
            price REAL,
            liquidity REAL,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            volume_h24 REAL,
            narrative_score INTEGER,
            discovery_score INTEGER,
            momentum_score INTEGER,
            risk_level TEXT,
            stage TEXT,
            PRIMARY KEY(address, scanned_at)
        )
        """
    ]

    with db_connect() as con:
        for statement in schema:
            con.execute(statement)

        con.execute("""
            CREATE INDEX IF NOT EXISTS ix_radar_v51_address_date
            ON radar_v51(address, scanned_at)
        """)


# ==========================================================
# API
# ==========================================================

def http_json(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=18
    )
    response.raise_for_status()
    return response.json()


# ==========================================================
# GTA NARRATIVE ANALYSIS
# ==========================================================

def narrative(name, symbol, extra):
    primary = f"{name} {symbol}"

    if has_term(primary, STRONG):
        return 100, "GTA 6 in naam/ticker"

    if has_term(primary, RELATED):
        return 65, "GTA-gerelateerde naam"

    if has_term(extra, STRONG):
        return 75, "GTA 6 in profiel/socials"

    if has_term(extra, RELATED):
        return 35, "Zwakke GTA-context"

    return 0, "Geen GTA-context"


# ==========================================================
# DISCOVERY ENGINE
# ==========================================================

def collect_candidates():
    candidates = {}
    errors = []

    def add(address, source, profile=""):
        if not address:
            return

        if len(candidates) >= MAX_CANDIDATES and address not in candidates:
            return

        entry = candidates.setdefault(
            address,
            {"source": set(), "extra": ""}
        )

        entry["source"].add(source)
        entry["extra"] = (
            entry["extra"] + " " + str(profile or "")
        )[:3500]

    # DexScreener keyword discovery
    for term in SEARCH_TERMS:
        try:
            data = http_json(
                f"{DEX}/latest/dex/search",
                {"q": term}
            )

            for pair in data.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}

                add(
                    base.get("address"),
                    "DEX search",
                    f"{base.get('name', '')} {base.get('symbol', '')}"
                )

        except (requests.RequestException, ValueError) as exc:
            errors.append(f"DEX search {term}: {exc}")

    # DexScreener latest profiles and boosts
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]

    for endpoint in endpoints:
        try:
            data = http_json(DEX + endpoint)

            for item in data if isinstance(data, list) else []:
                if item.get("chainId") != "solana":
                    continue

                links = " ".join(
                    str(x.get("url", ""))
                    for x in item.get("links") or []
                )

                add(
                    item.get("tokenAddress"),
                    "DEX profiles/boosts",
                    f"{item.get('description', '')} {links}"
                )

        except (requests.RequestException, ValueError) as exc:
            errors.append(f"DEX {endpoint}: {exc}")

    # GeckoTerminal new Solana pools
    try:
        data = http_json(
            f"{GECKO}/networks/solana/new_pools",
            {"page": 1}
        )

        included = {
            x.get("id"): x.get("attributes") or {}
            for x in data.get("included") or []
        }

        for pool in data.get("data") or []:
            attrs = pool.get("attributes") or {}

            rel = (
                pool.get("relationships") or {}
            ).get("base_token") or {}

            tok = (rel.get("data") or {}).get("id", "")

            address = (
                tok.split("_", 1)[-1]
                if "_" in tok
                else ""
            )

            token_info = included.get(tok, {})

            token_text = (
                f"{token_info.get('name', '')} "
                f"{token_info.get('symbol', '')} "
                f"{attrs.get('name', '')}"
            )

            if address and (
                has_term(token_text, STRONG)
                or has_term(token_text, RELATED)
            ):
                add(
                    address,
                    "Gecko new pools",
                    token_text
                )

    except (
        requests.RequestException,
        ValueError,
        KeyError
    ) as exc:
        errors.append(f"Gecko new pools: {exc}")

    # Existing watchlist
    for address, name in db_all(
        "SELECT address, name FROM watchlist"
    ):
        add(address, "Watchlist", name)

    return candidates, errors


# ==========================================================
# MARKET DATA
# ==========================================================

def market_pairs(addresses):
    chosen = {}
    errors = []
    all_addresses = list(addresses)

    # DexScreener supports batches of up to 30.
    for start in range(0, len(all_addresses), 30):
        chunk = all_addresses[start:start + 30]

        try:
            pairs = http_json(
                f"{DEX}/tokens/v1/solana/{','.join(chunk)}"
            )

            if not isinstance(pairs, list):
                continue

            allowed = set(chunk)

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address", "")

                if address not in allowed:
                    continue

                liquidity = num(
                    (pair.get("liquidity") or {}).get("usd")
                )

                previous_liquidity = num(
                    (chosen.get(address, {}).get("liquidity") or {}).get("usd")
                )

                if (
                    address not in chosen
                    or liquidity > previous_liquidity
                ):
                    chosen[address] = pair

        except (requests.RequestException, ValueError) as exc:
            errors.append(
                f"DEX batch {start // 30 + 1}: {exc}"
            )

    return chosen, errors


def previous_reading(address):
    rows = db_all(
        """
        SELECT scanned_at, volume_m5, buys_m5,
               sells_m5, liquidity, price
        FROM radar_v51
        WHERE address=?
        ORDER BY scanned_at DESC
        LIMIT 1
        """,
        (address,)
    )

    return rows[0] if rows else None


# ==========================================================
# SCORING AND TREND DETECTION
# ==========================================================

def assess(address, pair, meta, timestamp):
    tok = pair.get("baseToken") or {}

    name = tok.get("name") or "Unknown"
    symbol = tok.get("symbol") or "?"

    info = pair.get("info") or {}

    links = " ".join(
        str(x.get("url", "")) + " " + str(x.get("handle", ""))
        for x in (
            (info.get("websites") or [])
            + (info.get("socials") or [])
        )
    )

    score, reason = narrative(
        name,
        symbol,
        meta["extra"] + " " + links
    )

    if not score:
        return None

    created = int(num(pair.get("pairCreatedAt")))

    age = (
        max(
            0,
            (
                datetime.fromisoformat(timestamp).timestamp() * 1000
                - created
            ) / 3600000
        )
        if created else None
    )

    liq = num(
        (pair.get("liquidity") or {}).get("usd")
    )

    v5 = num(
        (pair.get("volume") or {}).get("m5")
    )

    v24 = num(
        (pair.get("volume") or {}).get("h24")
    )

    tx5 = (
        (pair.get("txns") or {}).get("m5") or {}
    )

    buys = int(num(tx5.get("buys")))
    sells = int(num(tx5.get("sells")))

    price = num(pair.get("priceUsd"))

    tx24 = (
        (pair.get("txns") or {}).get("h24") or {}
    )

    buys24 = int(num(tx24.get("buys")))
    sells24 = int(num(tx24.get("sells")))

    prior = previous_reading(address)

    ratio = None
    comparable = False

    if prior:
        try:
            dt = (
                datetime.fromisoformat(timestamp)
                - datetime.fromisoformat(prior[0])
            ).total_seconds() / 60

            comparable = 4 <= dt <= 20

            if comparable and num(prior[1]) >= 5:
                ratio = v5 / num(prior[1])

        except (ValueError, TypeError):
            pass

    # Early Discovery Score
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

    # Momentum Score
    momentum = 0

    if buys + sells >= 5:
        momentum += 20

    if buys >= 5 and buys >= 2 * max(sells, 1):
        momentum += 30

    if v5 >= 100:
        momentum += 15

    if v5 >= 1000:
        momentum += 10

    if ratio is not None and ratio >= 2 and v5 >= 250:
        momentum += 25

    momentum = min(100, momentum)

    # Risk flags
    warnings = []

    if liq < 1000:
        warnings.append("Extreem lage liquiditeit")
    elif liq < 10000:
        warnings.append("Lage liquiditeit")

    if age is None:
        warnings.append("Pairleeftijd onbekend")

    if buys + sells < 5:
        warnings.append("Weinig transacties (5m)")

    if not links:
        warnings.append(
            "Socials/website ontbreken in pairgegevens"
        )

    warnings.append(
        "Contractbevoegdheden en holderverdeling niet geverifieerd"
    )

    risk = (
        "Hoog"
        if liq < 10000
        else "Onbekend — extra controles nodig"
    )

    # Classification
    if age is not None and age <= 24 and momentum < 45:
        stage = "Pre-trend kandidaat"
    elif momentum >= 60 and comparable:
        stage = "Early momentum"
    elif momentum >= 35:
        stage = "Activiteit zichtbaar"
    else:
        stage = "Monitoren"

    signals = []

    if ratio is not None and ratio >= 2 and v5 >= 250:
        signals.append("VOLUME_VERSNELLING")

    if buys >= 5 and buys >= 2 * max(sells, 1):
        signals.append("KOOPDRUK_5M")

    return {
        "Naam": name,
        "Ticker": symbol,
        "Adres": address,
        "Bron": ", ".join(sorted(meta["source"])),
        "Narratief": reason,
        "Narrative": score,
        "Early Discovery": discovery,
        "Momentum": momentum,
        "Risico": risk,
        "Fase": stage,
        "Leeftijd uren": round(age, 2) if age is not None else None,
        "Liquiditeit USD": liq,
        "Volume 5m USD": v5,
        "Volume 24u USD": v24,
        "Koop 5m": buys,
        "Verkoop 5m": sells,
        "Koop 24u": buys24,
        "Verkoop 24u": sells24,
        "Prijs USD": price,
        "Volume ratio": round(ratio, 2) if ratio is not None else None,
        "Signalen": ", ".join(signals),
        "Waarschuwingen": "; ".join(warnings),
        "DEX": pair.get("url") or "",
        "created": created
    }


# ==========================================================
# SAVE RESULTS
# ==========================================================

def persist(rows, timestamp, error_count):
    with db_connect() as con:
        for r in rows:
            a = r["Adres"]

            con.execute(
                """
                INSERT INTO discovered
                (address, name, symbol, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
                """,
                (
                    a, r["Naam"], r["Ticker"],
                    timestamp, timestamp
                )
            )

            con.execute(
                """
                INSERT OR IGNORE INTO discovery_events
                (address, detected_at, name, symbol,
                 pair_age_hours, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    a, timestamp, r["Naam"],
                    r["Ticker"], r["Leeftijd uren"],
                    r["Bron"]
                )
            )

            con.execute(
                """
                INSERT INTO snapshots
                (address, scanned_at, price, liquidity,
                 volume24, buys24, sells24)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a, timestamp, r["Prijs USD"],
                    r["Liquiditeit USD"],
                    r["Volume 24u USD"],
                    r["Koop 24u"], r["Verkoop 24u"]
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
                    a, timestamp, r["Volume 5m USD"],
                    r["Koop 5m"], r["Verkoop 5m"],
                    r["Liquiditeit USD"], r["Prijs USD"]
                )
            )

            con.execute(
                """
                INSERT OR REPLACE INTO radar_v51
                (address, scanned_at, name, symbol, source,
                 pair_created_at, age_hours, price, liquidity,
                 volume_m5, buys_m5, sells_m5, volume_h24,
                 narrative_score, discovery_score,
                 momentum_score, risk_level, stage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    a, timestamp, r["Naam"], r["Ticker"],
                    r["Bron"], r["created"],
                    r["Leeftijd uren"], r["Prijs USD"],
                    r["Liquiditeit USD"], r["Volume 5m USD"],
                    r["Koop 5m"], r["Verkoop 5m"],
                    r["Volume 24u USD"], r["Narrative"],
                    r["Early Discovery"], r["Momentum"],
                    r["Risico"], r["Fase"]
                )
            )

            for signal in filter(
                None, r["Signalen"].split(", ")
            ):
                con.execute(
                    """
                    INSERT OR IGNORE INTO token_signals
                    (address, scanned_at, signal, detail)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        a, timestamp, signal,
                        "V5.1 heuristiek; geen handelsadvies"
                    )
                )

        con.execute(
            """
            INSERT OR REPLACE INTO scan_runs
            (scanned_at, candidates, errors)
            VALUES (?, ?, ?)
            """,
            (timestamp, len(rows), error_count)
        )


# ==========================================================
# SCAN ENGINE
# ==========================================================

def run_scan():
    timestamp = now()

    candidate_map, errors = collect_candidates()

    pairs, pair_errors = market_pairs(candidate_map)

    errors.extend(pair_errors)

    rows = []

    for address, pair in pairs.items():
        try:
            row = assess(
                address,
                pair,
                candidate_map[address],
                timestamp
            )

            if row:
                rows.append(row)

        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                f"Analyse {address[:12]}: {exc}"
            )

    persist(rows, timestamp, len(errors))

    return rows, errors, timestamp


def last_stored():
    result = db_all(
        """
        SELECT scanned_at
        FROM radar_v51
        ORDER BY scanned_at DESC
        LIMIT 1
        """
    )

    if not result:
        return pd.DataFrame()

    stamp = result[0][0]

    rows = db_all(
        """
        SELECT name, symbol, address, source, age_hours,
               price, liquidity, volume_m5, volume_h24,
               buys_m5, sells_m5, narrative_score,
               discovery_score, momentum_score,
               risk_level, stage
        FROM radar_v51
        WHERE scanned_at=?
        """,
        (stamp,)
    )

    return pd.DataFrame(
        rows,
        columns=[
            "Naam", "Ticker", "Adres", "Bron",
            "Leeftijd uren", "Prijs USD",
            "Liquiditeit USD", "Volume 5m USD",
            "Volume 24u USD", "Koop 5m",
            "Verkoop 5m", "Narrative",
            "Early Discovery", "Momentum",
            "Risico", "Fase"
        ]
    )


# ==========================================================
# STREAMLIT DASHBOARD
# ==========================================================

init_db()

st.title("🎮 GTA 6 Narrative Trading Radar V5.1")

st.caption(
    "Solana · GTA-gerelateerde memecoins · "
    "Early Discovery + Momentum · "
    "Onderzoek / geen echte orders"
)

st.warning(
    "Een score is geen winstverwachting. "
    "Nieuw token ≠ veilig token. "
    "Scams, wash trading en plotselinge "
    "liquiditeitsverliezen zijn mogelijk."
)

st.sidebar.header("⚙️ Instellingen")

auto = st.sidebar.toggle(
    "Automatische scan elke 5 minuten (actieve sessie)",
    value=False
)

max_age = st.sidebar.slider(
    "Early Discovery: max. leeftijd in uren",
    1, 168, 24
)

min_liq = st.sidebar.number_input(
    "Minimum liquiditeit shortlist ($)",
    min_value=0,
    value=10000,
    step=1000
)

st.sidebar.caption(
    "Streamlit Community Cloud is niet geschikt "
    "voor gegarandeerde 24/7-scans of "
    "permanente SQLite-opslag."
)

if "v51_rows" not in st.session_state:
    st.session_state.v51_rows = None

if "v51_errors" not in st.session_state:
    st.session_state.v51_errors = []

if "v51_time" not in st.session_state:
    st.session_state.v51_time = None

if "v51_last_attempt" not in st.session_state:
    st.session_state.v51_last_attempt = 0.0


@st.fragment(run_every="300s")
def radar_dashboard():
    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    due = (
        auto and (
            time.monotonic()
            - st.session_state.v51_last_attempt >= 300
        )
    )

    if manual or due:
        st.session_state.v51_last_attempt = time.monotonic()

        with st.spinner(
            "GTA-tokens ontdekken en analyseren..."
        ):
            try:
                rows, errors, stamp = run_scan()

                st.session_state.v51_rows = rows
                st.session_state.v51_errors = errors
                st.session_state.v51_time = stamp

            except (
                requests.RequestException,
                sqlite3.Error,
                ValueError
            ) as exc:
                st.error(f"Scanfout: {exc}")

    if st.session_state.v51_rows is not None:
        df = pd.DataFrame(st.session_state.v51_rows)
    else:
        df = last_stored()

    st.caption(
        "Laatste scan in deze sessie (UTC): "
        f"{st.session_state.v51_time or 'Nog niet uitgevoerd'}"
    )

    if df.empty:
        st.info(
            "Nog geen GTA-kandidaten. "
            "Klik op 'Scan nu'."
        )
        return

    a, b, c = st.columns(3)

    a.metric("GTA-kandidaten", len(df))

    b.metric(
        f"Nieuwe paren (≤{max_age} uur)",
        int(
            (
                df["Leeftijd uren"].fillna(1e9)
                <= max_age
            ).sum()
        )
    )

    c.metric(
        "Momentum ≥60",
        int((df["Momentum"] >= 60).sum())
    )

    st.subheader(
        "🚀 Early Discovery — recent aangemaakte paren"
    )

    young = df[
        df["Leeftijd uren"].notna()
        & (df["Leeftijd uren"] <= max_age)
    ].copy()

    st.dataframe(
        young.sort_values("Leeftijd uren")
        if not young.empty else young,
        use_container_width=True,
        hide_index=True
    )

    st.subheader("📈 Activiteit en trends")

    active = df[
        df["Liquiditeit USD"] >= min_liq
    ].sort_values(
        ["Momentum", "Early Discovery"],
        ascending=False
    )

    st.dataframe(
        active,
        use_container_width=True,
        hide_index=True
    )

    with st.expander("Alle GTA-kandidaten"):
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

    options = {
        f"{r['Naam']} ({r['Ticker']}) — {r['Adres'][:8]}": r["Adres"]
        for _, r in df.iterrows()
    }

    selected = st.selectbox(
        "🔍 Token bekijken",
        list(options),
        key="v51_selection"
    )

    addr = options[selected]

    token = df[
        df["Adres"] == addr
    ].iloc[0]

    st.code(addr, language="text")

    st.write(
        "Fase:", token["Fase"],
        "| Risico:", token["Risico"]
    )

    st.caption(
        "Risicocontroles zijn onvolledig: "
        "mint/freeze-authority, LP-lock, "
        "top holders en verkoopbaarheid "
        "zijn niet geverifieerd."
    )

    if st.button(
        "➕ Op watchlist",
        key="v51_watch"
    ):
        with db_connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO watchlist
                (address, name, added_at)
                VALUES (?, ?, ?)
                """,
                (addr, token["Naam"], now())
            )

        st.success("Toegevoegd")

    st.link_button(
        "Open op DexScreener",
        f"https://dexscreener.com/solana/{addr}"
    )

    past = db_all(
        """
        SELECT scanned_at, price,
               volume_m5, liquidity
        FROM radar_v51
        WHERE address=?
        ORDER BY scanned_at DESC
        LIMIT 100
        """,
        (addr,)
    )

    if len(past) >= 2:
        hist = pd.DataFrame(
            past,
            columns=[
                "Tijd", "Prijs",
                "Volume 5m", "Liquiditeit"
            ]
        )

        hist["Tijd"] = pd.to_datetime(
            hist["Tijd"],
            utc=True
        )

        hist = hist.sort_values("Tijd").set_index("Tijd")

        st.line_chart(hist[["Prijs"]])

        st.line_chart(hist[["Volume 5m"]])

    st.download_button(
        "📥 Download scan-CSV",
        df.to_csv(index=False).encode("utf-8"),
        "gta6_radar_v51.csv",
        "text/csv"
    )

    if st.session_state.v51_errors:
        with st.expander(
            f"⚠️ API-meldingen "
            f"({len(st.session_state.v51_errors)})"
        ):
            st.write(
                st.session_state.v51_errors[:30]
            )


radar_dashboard()


# ==========================================================
# HISTORY AND WATCHLIST
# ==========================================================

st.divider()

st.subheader("📣 Historische signalen")

signals = db_all(
    """
    SELECT scanned_at, address, signal
    FROM token_signals
    ORDER BY scanned_at DESC
    LIMIT 50
    """
)

if signals:
    st.dataframe(
        pd.DataFrame(
            signals,
            columns=["UTC", "Mint", "Signaal"]
        ),
        hide_index=True,
        use_container_width=True
    )

st.subheader("⭐ Watchlist")

watch = db_all(
    """
    SELECT name, address, added_at
    FROM watchlist
    ORDER BY added_at DESC
    LIMIT 50
    """
)

if watch:
    st.dataframe(
        pd.DataFrame(
            watch,
            columns=[
                "Naam", "Mint", "Toegevoegd"
            ]
        ),
        hide_index=True,
        use_container_width=True
    )

st.caption(
    "V5.1 registreert signalen, maar verstuurt "
    "geen orders. Nieuws/sociale trending-feed "
    "en paper trading volgen in latere versies."
)
