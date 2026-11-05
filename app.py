
"""GTA 6 Narrative Radar V5.3: persistent tracking and manual paper trading."""

import os
import re
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GTA 6 Radar V5.3",
    page_icon="🎮",
    layout="wide"
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2"

WORDS = [
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

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GTA6-Radar-V5.3",
    "Accept": "application/json"
})


# ==================================================
# HELPERS
# ==================================================

def now():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def numeric(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def n(x):
    return numeric(x) or 0.0


def matches(text, phrases):
    value = str(text or "").lower()
    return any(
        re.search(
            r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
            value
        )
        for term in phrases
    )


# ==================================================
# DATABASE
# ==================================================

def db():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def read(sql, params=()):
    with db() as con:
        return con.execute(sql, params).fetchall()


def setup():
    # Existing tables and data are preserved.
    statements = [
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
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
        CREATE TABLE IF NOT EXISTS token_signals (
            address TEXT,
            scanned_at TEXT,
            signal TEXT,
            detail TEXT,
            PRIMARY KEY(address, scanned_at, signal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS radar_v53 (
            address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT,
            symbol TEXT,
            source TEXT,
            status TEXT,
            pair_address TEXT,
            age_hours REAL,
            price REAL,
            liquidity REAL,
            volume_m5 REAL,
            buys_m5 INTEGER,
            sells_m5 INTEGER,
            narrative INTEGER,
            momentum INTEGER,
            phase TEXT,
            warnings TEXT,
            PRIMARY KEY(address, scanned_at)
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
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT,
            opened_at TEXT NOT NULL,
            entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL,
            usd_amount REAL NOT NULL,
            tokens REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            closed_at TEXT,
            exit_market REAL,
            exit_effective REAL,
            net_pnl REAL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_v53_address_time
        ON radar_v53(address, scanned_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_paper_status
        ON paper_trades(status)
        """
    ]

    with db() as con:
        for statement in statements:
            con.execute(statement)


# ==================================================
# API
# ==================================================

def request_json(url, params=None):
    result = SESSION.get(
        url,
        params=params,
        timeout=18
    )
    result.raise_for_status()
    return result.json()


# ==================================================
# TOKEN DISCOVERY
# ==================================================

def discover():
    found = {}
    issues = []

    def add(address, source, extra=""):
        if not address or not ADDRESS.fullmatch(address):
            return

        item = found.setdefault(
            address,
            {"sources": set(), "extra": ""}
        )

        item["sources"].add(source)
        item["extra"] = (
            item["extra"] + " " + str(extra)
        )[:2000]

    # DexScreener keyword search
    for word in WORDS:
        try:
            data = request_json(
                DEX + "/latest/dex/search",
                {"q": word}
            )

            for pair in data.get("pairs") or []:
                if pair.get("chainId") == "solana":
                    token = pair.get("baseToken") or {}

                    add(
                        token.get("address"),
                        "DEX search",
                        f"{token.get('name', '')} "
                        f"{token.get('symbol', '')}"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:
            issues.append(f"Search {word}: {error}")

    # DexScreener latest profiles and boosts
    for endpoint in [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1"
    ]:
        try:
            data = request_json(DEX + endpoint)

            for item in data if isinstance(data, list) else []:
                if item.get("chainId") == "solana":
                    links = " ".join(
                        str(x.get("url", ""))
                        for x in item.get("links") or []
                    )

                    add(
                        item.get("tokenAddress"),
                        "DEX profile",
                        str(item.get("description") or "")
                        + " " + links
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:
            issues.append(
                f"Profiles {endpoint}: {error}"
            )

    # GeckoTerminal new pools
    try:
        data = request_json(
            GECKO + "/networks/solana/new_pools",
            {
                "include": "base_token,quote_token",
                "page": 1
            }
        )

        included = {
            x.get("id"): x.get("attributes") or {}
            for x in data.get("included") or []
        }

        for pool in data.get("data") or []:
            rel = (
                (pool.get("relationships") or {})
                .get("base_token") or {}
            )

            token_id = (
                (rel.get("data") or {})
                .get("id", "")
            )

            token = included.get(token_id, {})

            address = token.get("address") or (
                token_id.split("_", 1)[-1]
                if "_" in token_id
                else ""
            )

            description = " ".join([
                str(token.get("name") or ""),
                str(token.get("symbol") or ""),
                str(
                    (pool.get("attributes") or {})
                    .get("name") or ""
                )
            ])

            if matches(description, STRONG + RELATED):
                add(
                    address,
                    "Gecko new pools",
                    description
                )

    except (
        requests.RequestException,
        ValueError,
        TypeError
    ) as error:
        issues.append(f"Gecko: {error}")

    return found, issues


# ==================================================
# PERSISTENT TRACKING
# ==================================================

def pending_addresses(found, max_follow=250):
    # Rotate older tokens so they stay monitored.
    historical = [
        x[0] for x in read(
            """
            SELECT address FROM discovered
            ORDER BY COALESCE(last_seen, first_seen) ASC
            """
        )
    ]

    watch = [
        x[0] for x in read(
            """
            SELECT address FROM watchlist
            ORDER BY added_at DESC
            """
        )
    ]

    tracked = [
        x[0] for x in read(
            """
            SELECT address FROM tracking_state
            ORDER BY COALESCE(last_checked, first_seen) ASC
            """
        )
    ]

    follow = list(
        dict.fromkeys(watch + tracked + historical)
    )[:max_follow]

    all_addresses = list(
        dict.fromkeys(list(found) + follow)
    )

    for address in follow:
        found.setdefault(
            address,
            {"sources": set(), "extra": ""}
        )["sources"].add("Historisch gevolgd")

    return all_addresses


# ==================================================
# MARKET DATA
# ==================================================

def market_data(addresses):
    best = {}
    errors = []
    successful = set()

    for start in range(0, len(addresses), 30):
        chunk = addresses[start:start + 30]

        try:
            pairs = request_json(
                DEX + "/tokens/v1/solana/"
                + ",".join(chunk)
            )

            if not isinstance(pairs, list):
                raise ValueError(
                    "Unexpected response type"
                )

            successful.update(chunk)

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address")

                if address not in chunk:
                    continue

                current = best.get(address)

                if (
                    current is None
                    or n(
                        (pair.get("liquidity") or {})
                        .get("usd")
                    ) > n(
                        (current.get("liquidity") or {})
                        .get("usd")
                    )
                ):
                    best[address] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as error:
            errors.append(
                f"Market batch {start // 30 + 1}: {error}"
            )

    return best, successful, errors


# ==================================================
# MOMENTUM INTELLIGENCE
# ==================================================

def trend(address, stamp, price, volume):
    previous = read(
        """
        SELECT scanned_at, price, volume_m5
        FROM radar_v53
        WHERE address=? AND status='ACTIEF'
        ORDER BY scanned_at DESC
        LIMIT 2
        """,
        (address,)
    )

    if len(previous) < 2 or not price or not volume:
        return 0, "Onvoldoende vergelijkbare metingen"

    a, b = previous[0], previous[1]

    try:
        delta1 = (
            datetime.fromisoformat(stamp)
            - datetime.fromisoformat(a[0])
        ).total_seconds() / 60

        delta2 = (
            datetime.fromisoformat(a[0])
            - datetime.fromisoformat(b[0])
        ).total_seconds() / 60

    except (TypeError, ValueError):
        return 0, "Ongeldige tijdstempels"

    if not (
        4 <= delta1 <= 12
        and 4 <= delta2 <= 12
    ):
        return 0, "Metingen niet rond vijf minuten"

    if not all(
        n(x) > 0
        for x in [a[1], b[1], a[2], b[2]]
    ):
        return 0, "Ontbrekende prijs- of volumemetingen"

    rising_price = (
        n(b[1]) < n(a[1]) < price
        and price >= n(b[1]) * 1.02
    )

    rising_volume = (
        n(b[2]) < n(a[2]) < volume
        and volume >= 250
    )

    if rising_price and rising_volume:
        return (
            80,
            "Prijs en 5m-volume stijgen gedurende 3 metingen"
        )

    if rising_price or rising_volume:
        return (
            30,
            "Slechts 1 van 2 trendvoorwaarden vervuld"
        )

    return 0, "Geen bevestigd opwaarts momentum"


# ==================================================
# TOKEN ANALYSIS
# ==================================================

def analyze(address, pair, meta, stamp):
    token = pair.get("baseToken") or {}

    name = str(token.get("name") or "Unknown")
    symbol = str(token.get("symbol") or "?")

    info = pair.get("info") or {}

    links = " ".join(
        str(x.get("url", ""))
        for x in (
            (info.get("websites") or [])
            + (info.get("socials") or [])
        )
    )

    primary = f"{name} {symbol}"
    context = meta["extra"] + " " + links

    score = (
        100 if matches(primary, STRONG)
        else 65 if matches(primary, RELATED)
        else 75 if matches(context, STRONG)
        else 35 if matches(context, RELATED)
        else 0
    )

    # Continue tracking historical tokens.
    if (
        score == 0
        and "Historisch gevolgd" not in meta["sources"]
    ):
        return None

    created = numeric(pair.get("pairCreatedAt"))

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

    liquidity = numeric(
        (pair.get("liquidity") or {}).get("usd")
    )

    price = numeric(pair.get("priceUsd"))

    volume = numeric(
        (pair.get("volume") or {}).get("m5")
    )

    tx = (pair.get("txns") or {}).get("m5") or {}

    buys = int(n(tx.get("buys")))
    sells = int(n(tx.get("sells")))

    momentum, evidence = trend(
        address, stamp, price, volume
    )

    warnings = []

    if liquidity is None:
        warnings.append(
            "Liquiditeit ontbreekt (NIET gelijk aan $0)"
        )
    elif liquidity < 10000:
        warnings.append(
            "Liquiditeit onder $10.000"
        )

    if (
        buys + sells >= 100
        and volume is not None
        and volume / (buys + sells) < 3
    ):
        warnings.append(
            "Veel kleine transacties; automatisering mogelijk"
        )

    warnings.append(
        "Contract, holders en verkoopbaarheid niet geverifieerd"
    )

    phase = (
        "MOMENTUM TER CONTROLE"
        if (
            momentum == 80
            and liquidity is not None
            and liquidity >= 10000
        )
        else "PRE-TREND KANDIDAAT"
        if age is not None and age < 24
        else "MONITOREN"
    )

    return {
        "Adres": address,
        "Naam": name,
        "Ticker": symbol,
        "Bron": ", ".join(sorted(meta["sources"])),
        "Status": "ACTIEF",
        "Fase": phase,
        "Narrative": score,
        "Momentum": momentum,
        "Bewijs": evidence,
        "Leeftijd": (
            round(age, 2)
            if age is not None else None
        ),
        "Prijs": price,
        "Liquiditeit": liquidity,
        "Volume5m": volume,
        "Kopen5m": buys,
        "Verkopen5m": sells,
        "Risico": "; ".join(warnings),
        "Pair": pair.get("pairAddress") or ""
    }


# ==================================================
# SAVE RESULTS
# ==================================================

def save_scan(rows, missing, stamp):
    with db() as con:
        for r in rows:
            a = r["Adres"]

            con.execute(
                """
                INSERT INTO discovered
                (address,name,symbol,first_seen,last_seen)
                VALUES (?,?,?,?,?)
                ON CONFLICT(address) DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
                """,
                (
                    a,
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
                (a, stamp, stamp, stamp)
            )

            con.execute(
                """
                INSERT INTO radar_v53
                (address,scanned_at,name,symbol,source,
                 status,pair_address,age_hours,price,
                 liquidity,volume_m5,buys_m5,sells_m5,
                 narrative,momentum,phase,warnings)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    a,
                    stamp,
                    r["Naam"],
                    r["Ticker"],
                    r["Bron"],
                    "ACTIEF",
                    r["Pair"],
                    r["Leeftijd"],
                    r["Prijs"],
                    r["Liquiditeit"],
                    r["Volume5m"],
                    r["Kopen5m"],
                    r["Verkopen5m"],
                    r["Narrative"],
                    r["Momentum"],
                    r["Fase"],
                    r["Risico"]
                )
            )

            if r["Fase"] == "MOMENTUM TER CONTROLE":
                con.execute(
                    """
                    INSERT OR IGNORE INTO token_signals
                    (address,scanned_at,signal,detail)
                    VALUES(?,?,?,?)
                    """,
                    (
                        a,
                        stamp,
                        "V53_MOMENTUM",
                        r["Bewijs"]
                    )
                )

        for address in missing:
            con.execute(
                """
                INSERT INTO tracking_state
                (address,first_seen,last_checked,
                 missed_scans,status)
                VALUES(?,?,?,1,'NIET_GEVONDEN')
                ON CONFLICT(address) DO UPDATE SET
                    last_checked=excluded.last_checked,
                    missed_scans=missed_scans+1,
                    status='NIET_GEVONDEN'
                """,
                (address, stamp, stamp)
            )


# ==================================================
# MAIN SCAN
# ==================================================

def scan():
    stamp = now()

    found, errors = discover()

    addresses = pending_addresses(found)

    pairs, successful, market_errors = market_data(
        addresses
    )

    errors.extend(market_errors)

    rows = []

    for address, pair in pairs.items():
        try:
            row = analyze(
                address,
                pair,
                found[address],
                stamp
            )

            if row:
                rows.append(row)

        except (
            ValueError,
            KeyError,
            TypeError
        ) as error:
            errors.append(
                f"Analyse {address[:10]}: {error}"
            )

    # API failures are not counted as missing.
    missing = [
        a for a in successful
        if a not in pairs
        and read(
            "SELECT 1 FROM discovered WHERE address=?",
            (a,)
        )
    ]

    save_scan(rows, missing, stamp)

    return (
        pd.DataFrame(rows),
        errors,
        stamp,
        len(missing)
    )


# ==================================================
# PAPER TRADING
# ==================================================

def paper_open(address, name, usd, price, fee, slip):
    # Simulated budget includes entry fee.
    effective = price * (1 + slip)

    units = usd * (1 - fee) / effective

    with db() as con:
        con.execute(
            """
            INSERT INTO paper_trades
            (address,name,opened_at,entry_market,
             entry_effective,usd_amount,tokens,
             fee_rate,slippage_rate,status)
            VALUES(?,?,?,?,?,?,?,?,?,'OPEN')
            """,
            (
                address,
                name,
                now(),
                price,
                effective,
                usd,
                units,
                fee,
                slip
            )
        )


def paper_close(trade_id, price):
    records = read(
        """
        SELECT tokens,usd_amount,fee_rate,slippage_rate
        FROM paper_trades
        WHERE id=? AND status='OPEN'
        """,
        (trade_id,)
    )

    if not records:
        raise ValueError(
            "Virtuele positie is al gesloten"
        )

    units, spent, fee, slip = records[0]

    effective = price * (1 - slip)

    proceeds = units * effective * (1 - fee)

    with db() as con:
        con.execute(
            """
            UPDATE paper_trades
            SET status='CLOSED',
                closed_at=?,
                exit_market=?,
                exit_effective=?,
                net_pnl=?
            WHERE id=? AND status='OPEN'
            """,
            (
                now(),
                price,
                effective,
                proceeds - spent,
                trade_id
            )
        )


# ==================================================
# STREAMLIT DASHBOARD
# ==================================================

setup()

st.title("🎮 GTA 6 Narrative Radar V5.3")

st.caption(
    "Persistent token tracking • Momentum • "
    "Handmatige paper trading • Geen echte orders"
)

st.warning(
    "Onderzoeksinstrument: lage of ontbrekende liquiditeit "
    "en ongeteste contracten maken veel tokens ongeschikt "
    "om te verhandelen. Virtuele prijzen garanderen geen "
    "uitvoerbare transacties."
)

auto = st.sidebar.toggle(
    "Auto-scan (actieve sessie, 5 minuten)",
    False
)

min_liq = st.sidebar.number_input(
    "Minimum liquiditeit voor paper trading ($)",
    min_value=1000,
    value=10000,
    step=1000
)

fee_pct = st.sidebar.number_input(
    "Gesimuleerde fee per zijde (%)",
    min_value=0.0,
    value=1.0,
    step=0.25
)

slip_pct = st.sidebar.number_input(
    "Gesimuleerde slippage per zijde (%)",
    min_value=0.0,
    value=2.0,
    step=0.5
)

st.sidebar.caption(
    "Community Cloud biedt geen gegarandeerde 24/7-scans "
    "of permanente SQLite-data. Gebruik later een externe "
    "database en worker."
)

defaults = {
    "scan_df": pd.DataFrame(),
    "scan_time": "Nog niet",
    "scan_errors": [],
    "misses": 0,
    "last_attempt": 0.0
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


@st.fragment(run_every="300s")
def dashboard():
    manual = st.button(
        "🔄 Scan nu",
        type="primary"
    )

    due = (
        auto
        and time.monotonic()
        - st.session_state.last_attempt >= 300
    )

    if manual or due:
        st.session_state.last_attempt = time.monotonic()

        with st.spinner(
            "Nieuwe én eerder ontdekte tokens controleren..."
        ):
            try:
                data, errors, stamp, missing_count = scan()

                st.session_state.scan_df = data
                st.session_state.scan_errors = errors
                st.session_state.scan_time = stamp
                st.session_state.misses = missing_count

            except (
                requests.RequestException,
                sqlite3.Error,
                ValueError
            ) as exc:
                st.error(f"Scanfout: {exc}")

    st.caption(
        f"Laatste scan (UTC): {st.session_state.scan_time}"
    )

    df = st.session_state.scan_df

    tracked = read(
        """
        SELECT address,status,missed_scans,last_found
        FROM tracking_state
        ORDER BY COALESCE(last_checked,first_seen) DESC
        LIMIT 300
        """
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Actief in laatste scan",
        len(df)
    )

    c2.metric(
        "Bewaarde tokens",
        read("SELECT COUNT(*) FROM discovered")[0][0]
    )

    c3.metric(
        "Niet gevonden deze scan",
        st.session_state.misses
    )

    if not df.empty:
        young = df[
            df["Leeftijd"].notna()
            & (df["Leeftijd"] < 24)
        ]

        st.subheader(
            "🚀 Nieuwe paren (jonger dan 24 uur)"
        )

        st.dataframe(
            young.sort_values("Leeftijd"),
            hide_index=True,
            use_container_width=True
        )

        st.subheader(
            "📈 Momentum en opvolging"
        )

        st.dataframe(
            df.sort_values(
                ["Momentum", "Volume5m"],
                ascending=False
            ),
            hide_index=True,
            use_container_width=True
        )

        options = {
            f"{r['Naam']} — {r['Adres'][:10]}": r["Adres"]
            for _, r in df.iterrows()
        }

        choice = st.selectbox(
            "Token bekijken",
            options
        )

        address = options[choice]

        selected = df[
            df["Adres"] == address
        ].iloc[0]

        st.code(address)

        st.write(
            "**Trendsignaal:**",
            selected["Bewijs"]
        )

        st.write(
            "**Risicocontrole:**",
            selected["Risico"]
        )

        st.link_button(
            "Bekijk op DexScreener",
            "https://dexscreener.com/solana/"
            + (selected["Pair"] or address)
        )

        if st.button(
            "⭐ Toevoegen aan watchlist"
        ):
            with db() as con:
                con.execute(
                    """
                    INSERT OR IGNORE INTO watchlist
                    (address,name,added_at)
                    VALUES(?,?,?)
                    """,
                    (
                        address,
                        selected["Naam"],
                        now()
                    )
                )

            st.success("Toegevoegd")

        history = read(
            """
            SELECT scanned_at,price,volume_m5,liquidity
            FROM radar_v53
            WHERE address=? AND status='ACTIEF'
            ORDER BY scanned_at DESC
            LIMIT 100
            """,
            (address,)
        )

        if len(history) > 1:
            h = pd.DataFrame(
                history,
                columns=[
                    "UTC",
                    "Prijs",
                    "Volume5m",
                    "Liquiditeit"
                ]
            )

            h["UTC"] = pd.to_datetime(
                h["UTC"],
                utc=True
            )

            h = h.sort_values("UTC").set_index("UTC")

            st.line_chart(h[["Prijs"]])

            st.line_chart(h[["Volume5m"]])

        # Manual paper trading
        st.subheader(
            "🧪 Open handmatige paper trade"
        )

        budget = st.number_input(
            "Virtueel budget ($)",
            min_value=5.0,
            value=25.0,
            step=5.0
        )

        ok = (
            selected["Prijs"] is not None
            and selected["Prijs"] > 0
            and selected["Liquiditeit"] is not None
            and selected["Liquiditeit"] >= min_liq
        )

        if not ok:
            st.info(
                "Paper-entry geblokkeerd: prijs ontbreekt "
                "of liquiditeit is onder de ingestelde grens."
            )

        if st.button(
            "Virtueel kopen",
            disabled=not ok
        ):
            paper_open(
                address,
                selected["Naam"],
                budget,
                float(selected["Prijs"]),
                fee_pct / 100,
                slip_pct / 100
            )

            st.success(
                "Virtuele positie geopend — er is niets "
                "op de blockchain gekocht."
            )

        st.download_button(
            "📥 CSV scan",
            df.to_csv(index=False).encode("utf-8"),
            "gta6_v53.csv",
            "text/csv"
        )

    # Tracking dashboard
    st.subheader("🗂️ Persistent tracking")

    if tracked:
        st.dataframe(
            pd.DataFrame(
                tracked,
                columns=[
                    "Adres",
                    "Status",
                    "Gemiste scans",
                    "Laatst gevonden"
                ]
            ),
            hide_index=True,
            use_container_width=True
        )

    # Paper positions
    st.subheader("📊 Virtuele posities")

    trades = read(
        """
        SELECT id,name,address,opened_at,usd_amount,
               entry_market,tokens,status,net_pnl
        FROM paper_trades
        ORDER BY id DESC
        LIMIT 100
        """
    )

    if trades:
        st.dataframe(
            pd.DataFrame(
                trades,
                columns=[
                    "ID",
                    "Token",
                    "Adres",
                    "Start",
                    "Inzet $",
                    "Instapprijs",
                    "Aantal",
                    "Status",
                    "Netto resultaat $"
                ]
            ),
            hide_index=True,
            use_container_width=True
        )

        open_trades = [
            r for r in trades
            if r[7] == "OPEN"
        ]

        if open_trades:
            options = {
                f"#{r[0]} {r[1]}": r
                for r in open_trades
            }

            label = st.selectbox(
                "Virtuele positie sluiten",
                list(options)
            )

            trade = options[label]

            prices = read(
                """
                SELECT price
                FROM radar_v53
                WHERE address=?
                  AND status='ACTIEF'
                  AND price>0
                ORDER BY scanned_at DESC
                LIMIT 1
                """,
                (trade[2],)
            )

            price = prices[0][0] if prices else None

            if price is not None:
                st.caption(
                    f"Laatste opgeslagen marktprijs: "
                    f"${price:.10g}. "
                    "Niet gegarandeerd uitvoerbaar."
                )

            if st.button(
                "Virtueel verkopen",
                disabled=price is None
            ):
                paper_close(
                    trade[0],
                    price
                )

                st.success(
                    "Virtuele positie gesloten. "
                    "Ververs de pagina om de tabel bij te werken."
                )

    if st.session_state.scan_errors:
        with st.expander(
            f"⚠️ API-meldingen "
            f"({len(st.session_state.scan_errors)})"
        ):
            st.write(
                st.session_state.scan_errors[:25]
            )


dashboard()

st.caption(
    "Momentum vereist drie vergelijkbare "
    "vijfminutenmetingen. 'Niet gevonden' betekent "
    "alleen dat een geslaagde API-query het token "
    "niet retourneerde."
)
