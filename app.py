
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# ==========================================================
# GTA 6 RADAR V5.4.3 — A/B PAPER TRADING + DATABASE HERSTEL
# ==========================================================

st.set_page_config(
    page_title="GTA 6 Radar V5.4.3",
    page_icon="🎮",
    layout="wide",
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
API = "https://api.dexscreener.com"
LOCAL = ZoneInfo("Europe/Brussels")

SEARCHES = [
    "GTA6", "GTA VI", "Grand Theft Auto", "Vice City",
    "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn"
]

STRONG = [
    "gta6", "gta 6", "gta vi", "gtavi",
    "grand theft auto", "grandtheftauto"
]

RELATED = [
    "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn", "raul bautista"
]

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6RadarResearch/5.4.3",
    "Accept": "application/json",
})


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def be_time(value):
    if not value:
        return "-"
    return parse_time(value).astimezone(LOCAL).strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def num(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def val(value):
    return num(value) or 0.0


def matches(text, words):
    text = str(text or "").lower()
    return any(word in text for word in words)


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def read(sql, args=()):
    with db() as conn:
        return conn.execute(sql, args).fetchall()


# ==========================================================
# DATABASE: BESTAANDE GEGEVENS BEHOUDEN
# ==========================================================

def initialize():
    definitions = {
        "discovered": """
            address TEXT PRIMARY KEY,
            name TEXT, symbol TEXT,
            first_seen TEXT, last_seen TEXT
        """,
        "watchlist": """
            address TEXT PRIMARY KEY,
            name TEXT, added_at TEXT
        """,
        "tracking_state": """
            address TEXT PRIMARY KEY,
            first_seen TEXT, last_checked TEXT,
            last_found TEXT, missed_scans INTEGER,
            status TEXT
        """,
        "radar_v54": """
            address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT, symbol TEXT,
            pair_address TEXT, source TEXT,
            price REAL, liquidity REAL,
            volume_m5 REAL, buys_m5 INTEGER,
            sells_m5 INTEGER, age_hours REAL,
            narrative INTEGER, momentum INTEGER,
            evidence TEXT, phase TEXT, warnings TEXT,
            PRIMARY KEY(address, scanned_at)
        """,
        "paper_v54": """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT, pair_address TEXT,
            opened_at TEXT,
            entry_market REAL, entry_effective REAL,
            amount_usd REAL, quantity REAL,
            fee_rate REAL, slippage_rate REAL,
            status TEXT DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL,
            exit_effective REAL, pnl_usd REAL,
            exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL
        """,
        "paper_v542_b": """
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT, pair_address TEXT,
            opened_at TEXT,
            entry_market REAL, entry_effective REAL,
            amount_usd REAL, quantity REAL,
            fee_rate REAL, slippage_rate REAL,
            status TEXT DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL,
            exit_effective REAL, pnl_usd REAL,
            exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL
        """,
        "v541_config": """
            name TEXT PRIMARY KEY, value TEXT
        """,
        "v542_decisions": """
            scan_at TEXT, address TEXT,
            name TEXT, strategy TEXT,
            outcome TEXT, reason TEXT,
            price REAL, liquidity REAL,
            PRIMARY KEY(scan_at, address, strategy)
        """,
        "v542_runs": """
            started_at TEXT PRIMARY KEY,
            finished_at TEXT, cause TEXT,
            tokens INTEGER, api_errors INTEGER,
            a_entries INTEGER, b_entries INTEGER,
            a_exits INTEGER, b_exits INTEGER
        """,
    }

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
            "b_exits": "INTEGER",
        },
        "v542_decisions": {
            "scan_at": "TEXT",
            "address": "TEXT",
            "name": "TEXT",
            "strategy": "TEXT",
            "outcome": "TEXT",
            "reason": "TEXT",
            "price": "REAL",
            "liquidity": "REAL",
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
            "warnings": "TEXT",
        },
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
        "peak_mark": "REAL",
    }

    migrations["paper_v54"] = paper_columns
    migrations["paper_v542_b"] = paper_columns

    with db() as conn:
        for table, columns in definitions.items():
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ({columns})"
            )

        for table, required in migrations.items():
            existing = {
                row[1] for row in conn.execute(
                    f"PRAGMA table_info({table})"
                )
            }

            for column, kind in required.items():
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} {kind}"
                    )

        existing_runs = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(v542_runs)"
            )
        }

        if "scan_at" in existing_runs:
            conn.execute("""
                UPDATE v542_runs
                SET started_at = scan_at
                WHERE started_at IS NULL
            """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_radar_history
            ON radar_v54(address, scanned_at)
        """)


def config_get(name, default):
    rows = read(
        "SELECT value FROM v541_config WHERE name=?",
        (name,),
    )
    return rows[0][0] == "1" if rows else default


def config_set(name, enabled):
    with db() as conn:
        conn.execute("""
            INSERT INTO v541_config(name, value)
            VALUES(?, ?)
            ON CONFLICT(name)
            DO UPDATE SET value=excluded.value
        """, (name, "1" if enabled else "0"))


# ==========================================================
# DEXSCREENER: TOKENS ONTDEKKEN
# ==========================================================

def api_get(path, params=None):
    response = HTTP.get(
        API + path,
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def discover():
    found = {}
    errors = []

    def add(address, source):
        if isinstance(address, str) and ADDRESS.fullmatch(address):
            found.setdefault(address, set()).add(source)

    for term in SEARCHES:
        try:
            payload = api_get(
                "/latest/dex/search",
                {"q": term},
            )

            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                token = pair.get("baseToken") or {}
                name = f"{token.get('name','')} {token.get('symbol','')}"

                if matches(name, STRONG + RELATED):
                    add(token.get("address"), "Search")

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Search {term}: {exc}")

    for path in [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]:
        try:
            payload = api_get(path)

            for item in payload if isinstance(payload, list) else []:
                if item.get("chainId") != "solana":
                    continue

                context = str(item.get("description") or "")
                links = item.get("links") or []

                for link in links:
                    if isinstance(link, dict):
                        context += " " + str(link.get("url") or "")

                if matches(context, STRONG + RELATED):
                    add(item.get("tokenAddress"), "Profile")

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Profile: {exc}")

    for table in ("paper_v54", "paper_v542_b"):
        for (address,) in read(
            f"SELECT address FROM {table} WHERE status='OPEN'"
        ):
            add(address, "Open paperpositie")

    for (address,) in read("SELECT address FROM watchlist"):
        add(address, "Watchlist")

    for (address,) in read("""
        SELECT address FROM discovered
        ORDER BY last_seen ASC LIMIT 200
    """):
        add(address, "Historiek")

    return found, errors


def fetch_pairs(addresses):
    pairs = {}
    errors = []

    for offset in range(0, len(addresses), 30):
        group = addresses[offset:offset + 30]

        try:
            payload = api_get(
                "/tokens/v1/solana/" + ",".join(group)
            )

            if not isinstance(payload, list):
                raise ValueError("Ongeldig API-antwoord")

            for pair in payload:
                if pair.get("chainId") != "solana":
                    continue

                address = (pair.get("baseToken") or {}).get("address")

                if address not in group:
                    continue

                current = pairs.get(address)
                liquidity = val(
                    (pair.get("liquidity") or {}).get("usd")
                )

                old_liquidity = (
                    val((current.get("liquidity") or {}).get("usd"))
                    if current else -1
                )

                if liquidity > old_liquidity:
                    pairs[address] = pair

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"Marktdata: {exc}")

    return pairs, errors


# ==========================================================
# MOMENTUM EN A/B-STRATEGIE
# ==========================================================

def history(address, pair, stamp):
    return read("""
        SELECT scanned_at, price, volume_m5
        FROM radar_v54
        WHERE address=?
          AND pair_address=?
          AND scanned_at<?
        ORDER BY scanned_at DESC
        LIMIT 40
    """, (address, pair, stamp))


def momentum(address, pair, stamp, price, volume):
    if not price or volume is None or volume <= 0:
        return False, False, "ONVOLLEDIGE_DATA"

    previous = [
        row for row in history(address, pair, stamp)
        if num(row[1]) and num(row[2]) and num(row[2]) > 0
    ]

    if len(previous) < 2:
        return (
            False, False,
            f"TE_WEINIG_METINGEN: {len(previous)+1}/3"
        )

    current_time = parse_time(stamp)

    recent = next(
        (
            row for row in previous
            if 3.5 <= (
                current_time - parse_time(row[0])
            ).total_seconds() / 60 <= 20
        ),
        None,
    )

    old = next(
        (
            row for row in previous
            if recent and 3.5 <= (
                parse_time(recent[0]) -
                parse_time(row[0])
            ).total_seconds() / 60 <= 20
        ),
        None,
    )

    if not recent or not old:
        return False, False, "ONGELDIG_INTERVAL"

    old_price = float(old[1])
    recent_price = float(recent[1])
    old_volume = float(old[2])
    recent_volume = float(recent[2])

    change = price / old_price - 1

    strategy_a = (
        old_price < recent_price < price
        and old_volume < recent_volume < volume
        and change >= 0.02
        and volume >= 250
    )

    strategy_b = (
        change >= 0.05
        and price > recent_price
        and volume >= 250
    )

    details = (
        f"GEMETEN: prijs {change:+.1%}; "
        f"5m-volume ${volume:,.0f}"
    )

    return strategy_a, strategy_b, details


def analyze(address, pair, sources, stamp):
    token = pair.get("baseToken") or {}

    name = str(token.get("name") or "Onbekend")
    symbol = str(token.get("symbol") or "")

    price = num(pair.get("priceUsd"))
    liquidity = num(
        (pair.get("liquidity") or {}).get("usd")
    )
    volume = num(
        (pair.get("volume") or {}).get("m5")
    )

    transactions = (pair.get("txns") or {}).get("m5") or {}
    buys = int(val(transactions.get("buys")))
    sells = int(val(transactions.get("sells")))

    created = num(pair.get("pairCreatedAt"))
    age = (
        max(
            0,
            (parse_time(stamp).timestamp() * 1000 - created)
            / 3600000,
        )
        if created else None
    )

    pair_address = str(pair.get("pairAddress") or "")
    name_text = f"{name} {symbol}"

    narrative = (
        100 if matches(name_text, STRONG)
        else 65 if matches(name_text, RELATED)
        else 0
    )

    a_signal, b_signal, reason = momentum(
        address, pair_address, stamp, price, volume
    )

    tx_count = buys + sells
    average = volume / tx_count if volume is not None and tx_count else None
    micro = bool(tx_count >= 100 and average is not None and average < 3)

    return {
        "address": address,
        "name": name,
        "symbol": symbol,
        "pair": pair_address,
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "age": age,
        "narrative": narrative,
        "a_signal": a_signal,
        "b_signal": b_signal,
        "reason": reason,
        "micro": micro,
        "source": ", ".join(sorted(sources)),
    }


# ==========================================================
# SCANRESULTATEN BEWAREN
# ==========================================================

def save_observations(records, stamp):
    with db() as conn:
        for r in records:
            conn.execute("""
                INSERT INTO discovered(
                    address,name,symbol,first_seen,last_seen
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(address)
                DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen
            """, (
                r["address"], r["name"],
                r["symbol"], stamp, stamp,
            ))

            conn.execute("""
                INSERT OR REPLACE INTO radar_v54(
                    address,scanned_at,name,symbol,
                    pair_address,source,price,liquidity,
                    volume_m5,buys_m5,sells_m5,
                    age_hours,narrative,momentum,
                    evidence,phase,warnings
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["address"], stamp,
                r["name"], r["symbol"],
                r["pair"], r["source"],
                r["price"], r["liquidity"],
                r["volume"], r["buys"], r["sells"],
                r["age"], r["narrative"],
                80 if r["a_signal"] else 0,
                r["reason"], "MONITOREN",
                "Contractveiligheid en verkoopbaarheid onbekend",
            ))


def eligible(r, strategy, used, settings):
    if r["address"] in used:
        return "GEEN_INSTAP", "Al eerder virtueel verhandeld"

    signal = (
        r["a_signal"] if strategy == "A"
        else r["b_signal"]
    )

    if not signal:
        return "GEEN_INSTAP", r["reason"]

    if not r["price"] or r["price"] <= 0:
        return "GEBLOKKEERD", "Ongeldige prijs"

    if not r["liquidity"] or r["liquidity"] < settings["min_liq"]:
        return "GEBLOKKEERD", "Liquiditeit te laag"

    if r["age"] is None or r["age"] > 24:
        return "GEBLOKKEERD", "Handelspaar te oud of leeftijd onbekend"

    if r["buys"] <= r["sells"]:
        return "GEBLOKKEERD", "Onvoldoende koopdruk"

    if strategy == "A" and r["micro"]:
        return "GEBLOKKEERD", "Verdachte microtransacties"

    return "KANDIDAAT", "Kwantitatieve voorwaarden voldaan"


# ==========================================================
# VIRTUELE POSITIES
# ==========================================================

def process_paper(records, stamp, settings, enabled):
    by_address = {r["address"]: r for r in records}
    counts = {"A": [0, 0], "B": [0, 0]}

    for strategy, table in (
        ("A", "paper_v54"),
        ("B", "paper_v542_b"),
    ):
        with db() as conn:
            trades = conn.execute(f"""
                SELECT id,address,pair_address,opened_at,
                       entry_market,quantity,amount_usd,
                       fee_rate,slippage_rate
                FROM {table}
                WHERE status='OPEN'
            """).fetchall()

            used = {
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT address FROM {table}"
                )
            }

            for (
                trade_id, address, original_pair,
                opened, entry, quantity, amount, fee, slip
            ) in trades:

                r = by_address.get(address)

                if not r or not r["price"]:
                    continue

                if original_pair and r["pair"] != original_pair:
                    conn.execute(
                        f"""UPDATE {table}
                            SET exit_reason='PAIR_CHANGED_CHECK_REQUIRED'
                            WHERE id=?""",
                        (trade_id,),
                    )
                    continue

                price = r["price"]

                conn.execute(
                    f"""UPDATE {table}
                        SET last_mark=?,last_mark_at=?,
                            peak_mark=MAX(COALESCE(peak_mark,0),?)
                        WHERE id=?""",
                    (price, stamp, price, trade_id),
                )

                change = price / entry - 1
                hours = (
                    parse_time(stamp) - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= settings["take"]
                    else "STOP_LOSS"
                    if change <= -settings["stop"]
                    else "TIME_EXIT"
                    if hours >= settings["max_hours"]
                    else None
                )

                if not reason:
                    continue

                if (
                    r["liquidity"] is None
                    or r["liquidity"] < settings["min_liq"]
                ):
                    conn.execute(
                        f"""UPDATE {table}
                            SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                            WHERE id=?""",
                        (trade_id,),
                    )
                    continue

                exit_effective = price * (1 - slip)
                pnl = (
                    quantity * exit_effective * (1 - fee)
                    - amount
                )

                conn.execute(
                    f"""UPDATE {table}
                        SET status='CLOSED',
                            closed_at=?,
                            exit_market=?,
                            exit_effective=?,
                            pnl_usd=?,
                            exit_reason=?
                        WHERE id=? AND status='OPEN'""",
                    (
                        stamp, price, exit_effective,
                        pnl, reason, trade_id,
                    ),
                )

                counts[strategy][1] += 1

            open_count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE status='OPEN'"
            ).fetchone()[0]

            for r in records:
                outcome, reason = eligible(
                    r, strategy, used, settings
                )

                if outcome == "KANDIDAAT":
                    if not enabled[strategy]:
                        outcome = "UITGESCHAKELD"
                        reason = "Paperstrategie uitgeschakeld"

                    elif open_count >= settings["max_positions"]:
                        outcome = "GEBLOKKEERD"
                        reason = "Maximum open posities bereikt"

                    else:
                        market_price = r["price"]
                        effective_price = (
                            market_price * (1 + settings["slip"])
                        )

                        quantity = (
                            settings["amount"] * (1 - settings["fee"])
                            / effective_price
                        )

                        conn.execute(
                            f"""INSERT INTO {table}(
                                address,name,pair_address,
                                opened_at,entry_market,
                                entry_effective,amount_usd,
                                quantity,fee_rate,slippage_rate,
                                status,last_mark,last_mark_at,peak_mark
                            )
                            VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?)""",
                            (
                                r["address"], r["name"],
                                r["pair"], stamp,
                                market_price, effective_price,
                                settings["amount"], quantity,
                                settings["fee"], settings["slip"],
                                market_price, stamp, market_price,
                            ),
                        )

                        open_count += 1
                        used.add(r["address"])
                        counts[strategy][0] += 1

                        outcome = "PAPER_ENTRY"
                        reason = "Virtuele instap; verkoopbaarheid niet bewezen"

                conn.execute("""
                    INSERT OR REPLACE INTO v542_decisions(
                        scan_at,address,name,strategy,
                        outcome,reason,price,liquidity
                    )
                    VALUES(?,?,?,?,?,?,?,?)
                """, (
                    stamp, r["address"], r["name"],
                    strategy, outcome, reason,
                    r["price"], r["liquidity"],
                ))

    return counts


# ==========================================================
# VOLLEDIGE SCAN
# ==========================================================

def scan(trigger, settings, enabled):
    stamp = utcnow()

    addresses, errors = discover()
    pairs, fetch_errors = fetch_pairs(list(addresses))
    errors.extend(fetch_errors)

    records = []

    for address, pair in pairs.items():
        try:
            r = analyze(
                address,
                pair,
                addresses[address],
                stamp,
            )

            if r["narrative"] >= 65 or "Open paperpositie" in addresses[address]:
                records.append(r)

        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"Analyse {address[:10]}: {exc}")

    save_observations(records, stamp)
    counts = process_paper(records, stamp, settings, enabled)

    finished = utcnow()

    with db() as conn:
        conn.execute("""
            INSERT INTO v542_runs(
                started_at,finished_at,cause,tokens,
                api_errors,a_entries,b_entries,a_exits,b_exits
            )
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            stamp, finished, trigger,
            len(records), len(errors),
            counts["A"][0], counts["B"][0],
            counts["A"][1], counts["B"][1],
        ))

    return len(records), counts, errors


# ==========================================================
# STREAMLIT DASHBOARD
# ==========================================================

initialize()

st.title("🎮 GTA 6 Radar V5.4.3")
st.caption(
    "A/B-paperstrategieën · Databaseherstel · Belgische tijd"
)

st.warning(
    "Alle transacties zijn virtueel. "
    "Een stoploss van 15% is geen gegarandeerd verliesmaximum."
)

for key, config, default in (
    ("auto", "auto_scan", True),
    ("paper_a", "paper", False),
    ("paper_b", "paper_b", False),
):
    if key not in st.session_state:
        st.session_state[key] = config_get(config, default)


st.sidebar.header("⚙️ Instellingen")

auto = st.sidebar.toggle(
    "Automatisch scannen",
    value=st.session_state.auto,
)
paper_a = st.sidebar.toggle(
    "Strategie A inschakelen",
    value=st.session_state.paper_a,
)
paper_b = st.sidebar.toggle(
    "Strategie B inschakelen",
    value=st.session_state.paper_b,
)

st.session_state.auto = auto
st.session_state.paper_a = paper_a
st.session_state.paper_b = paper_b

config_set("auto_scan", auto)
config_set("paper", paper_a)
config_set("paper_b", paper_b)

interval = st.sidebar.selectbox(
    "Scaninterval (minuten)",
    [5, 10, 15],
)

min_liq = st.sidebar.number_input(
    "Minimale liquiditeit ($)",
    min_value=1000.0,
    value=10000.0,
    step=1000.0,
)

stake = st.sidebar.number_input(
    "Virtuele inzet ($)",
    min_value=5.0,
    value=25.0,
    step=5.0,
)

max_positions = st.sidebar.number_input(
    "Maximum open posities",
    min_value=1,
    max_value=20,
    value=3,
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
    "Stoploss (%)", 5, 95, 15, 5
)

max_hours = st.sidebar.number_input(
    "Maximale looptijd (uren)", 1, 72, 4
)

settings = {
    "min_liq": min_liq,
    "amount": stake,
    "max_positions": max_positions,
    "fee": fee / 100,
    "slip": slip / 100,
    "take": take / 100,
    "stop": stop / 100,
    "max_hours": max_hours,
}

enabled = {"A": paper_a, "B": paper_b}


@st.fragment(run_every="60s")
def dashboard():
    manual = st.button("🔄 Scan nu", type="primary")

    last = read("""
        SELECT started_at,finished_at,cause
        FROM v542_runs
        WHERE started_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 1
    """)

    current = datetime.now(timezone.utc)

    last_finished = (
        parse_time(last[0][1])
        if last and last[0][1]
        else None
    )

    due = (
        auto
        and (
            last_finished is None
            or current >= last_finished + timedelta(minutes=interval)
        )
    )

    st.subheader("🕒 Scanplanning")

    if last:
        st.write("Laatste scan:", be_time(last[0][1]))
        st.write("Trigger:", last[0][2])
    else:
        st.info("Nog geen scans geregistreerd.")

    if manual or due:
        try:
            with st.spinner("GTA6-tokens verzamelen..."):
                count, result, errors = scan(
                    "MANUEEL" if manual else "AUTOMATISCH",
                    settings,
                    enabled,
                )

            st.success(
                f"{count} tokens · "
                f"A: {result['A'][0]} instappen · "
                f"B: {result['B'][0]} instappen"
            )

            if errors:
                with st.expander("API-meldingen"):
                    st.write(errors)

        except Exception as exc:
            st.error(f"Scannerfout: {exc}")

    latest = read("""
        SELECT started_at
        FROM v542_runs
        WHERE started_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 1
    """)

    if latest:
        rows = read("""
            SELECT name,address,price,liquidity,volume_m5,
                   buys_m5,sells_m5,momentum,evidence
            FROM radar_v54
            WHERE scanned_at=?
            ORDER BY volume_m5 DESC
        """, (latest[0][0],))

        tokens = pd.DataFrame(
            rows,
            columns=[
                "Naam", "Adres", "Prijs $",
                "Liquiditeit $", "Volume 5m $",
                "Buys", "Sells", "Momentum A",
                "Diagnose",
            ],
        )

        st.subheader("📡 Laatste tokenmeting")
        st.metric("Tokens", len(tokens))

        if not tokens.empty:
            st.dataframe(
                tokens,
                hide_index=True,
                use_container_width=True,
            )

            st.download_button(
                "Download tokenmetingen CSV",
                tokens.to_csv(index=False),
                "gta6_tokens.csv",
                mime="text/csv",
            )

            options = {
                f"{r['Naam']} · {r['Adres'][:10]}": r["Adres"]
                for _, r in tokens.iterrows()
            }

            selected = st.selectbox(
                "Bekijk tokenhistoriek",
                list(options),
            )

            address = options[selected]

            history_rows = read("""
                SELECT scanned_at,price,volume_m5
                FROM radar_v54
                WHERE address=?
                ORDER BY scanned_at DESC
                LIMIT 120
            """, (address,))

            if len(history_rows) >= 2:
                history_df = pd.DataFrame(
                    history_rows,
                    columns=["Tijd", "Prijs", "Volume"],
                )

                history_df["Tijd"] = pd.to_datetime(
                    history_df["Tijd"],
                    utc=True,
                )

                history_df = (
                    history_df.sort_values("Tijd")
                    .set_index("Tijd")
                )

                st.line_chart(history_df[["Prijs"]])
                st.line_chart(history_df[["Volume"]])

    st.subheader("🔎 A/B-beslissingen")

    if latest:
        decisions = read("""
            SELECT name,address,strategy,
                   outcome,reason,price,liquidity
            FROM v542_decisions
            WHERE scan_at=?
            ORDER BY strategy,name
        """, (latest[0][0],))

        decisions_df = pd.DataFrame(
            decisions,
            columns=[
                "Naam", "Adres", "Strategie",
                "Uitkomst", "Reden", "Prijs $",
                "Liquiditeit $",
            ],
        )

        st.dataframe(
            decisions_df,
            hide_index=True,
            use_container_width=True,
        )

        st.download_button(
            "Download A/B-beslissingen",
            decisions_df.to_csv(index=False),
            "gta6_ab_beslissingen.csv",
            mime="text/csv",
        )

    st.subheader("🧪 Paperportfolio's")

    tab_a, tab_b = st.tabs([
        "Strategie A — streng",
        "Strategie B — experimenteel",
    ])

    for tab, table in (
        (tab_a, "paper_v54"),
        (tab_b, "paper_v542_b"),
    ):
        with tab:
            trades = read(f"""
                SELECT id,name,address,opened_at,
                       amount_usd,entry_market,
                       last_mark,status,pnl_usd,
                       exit_reason
                FROM {table}
                ORDER BY id DESC
                LIMIT 300
            """)

            frame = pd.DataFrame(
                trades,
                columns=[
                    "ID", "Naam", "Adres",
                    "Geopend UTC", "Inzet $",
                    "Instap $", "Laatste prijs $",
                    "Status", "Winst/verlies $",
                    "Exitreden",
                ],
            )

            if frame.empty:
                st.info("Nog geen virtuele transacties.")
            else:
                st.dataframe(
                    frame,
                    hide_index=True,
                    use_container_width=True,
                )

                closed = frame[frame["Status"] == "CLOSED"]

                st.metric(
                    "Gerealiseerde paper-P/L",
                    f"${closed['Winst/verlies $'].sum():.2f}",
                )

                st.download_button(
                    f"Download {table}",
                    frame.to_csv(index=False),
                    f"{table}.csv",
                    mime="text/csv",
                )

    st.subheader("📋 Scanlogboek")

    logs = read("""
        SELECT cause,tokens,api_errors,
               a_entries,b_entries,a_exits,b_exits,
               started_at,finished_at
        FROM v542_runs
        WHERE started_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 50
    """)

    if logs:
        log_df = pd.DataFrame(
            logs,
            columns=[
                "Trigger", "Tokens", "API-fouten",
                "A-instappen", "B-instappen",
                "A-exits", "B-exits",
                "Start UTC", "Einde UTC",
            ],
        )

        log_df["Start België"] = (
            log_df["Start UTC"].apply(be_time)
        )
        log_df["Einde België"] = (
            log_df["Einde UTC"].apply(be_time)
        )

        st.dataframe(
            log_df.drop(columns=["Start UTC", "Einde UTC"]),
            hide_index=True,
            use_container_width=True,
        )


dashboard()

st.caption(
    "V5.4.3 — Papertrading, geen echte orders. "
    "Streamlit scant alleen betrouwbaar tijdens een actieve sessie. "
    "De afzonderlijke GitHub Learning Collector verzamelt "
    "gegevens onafhankelijk van deze app."
)
