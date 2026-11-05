
"""GTA6 Radar V5.4.2. Research scanner; A/B simulated trading only."""
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="GTA 6 Radar V5.4.2",
    page_icon="🎮",
    layout="wide",
)

DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
API = "https://api.dexscreener.com"
LOCAL = ZoneInfo("Europe/Brussels")

SEARCHES = [
    "GTA6",
    "GTA VI",
    "Grand Theft Auto",
    "Vice City",
    "Leonida",
    "Lucia Camino",
    "Jason Duval",
    "Port Gellhorn",
]

STRONG = [
    "gta6", "gta 6", "gta vi",
    "grand theft auto", "grandtheftauto", "gtavi",
]

RELATED = [
    "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn", "raul bautista",
]

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "GTA6RadarResearch/5.4.2",
    "Accept": "application/json",
})


# ============================================================
# TIME HELPERS
# ============================================================

def utcnow():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


def parse_time(value):
    value = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    return (
        value if value.tzinfo
        else value.replace(tzinfo=timezone.utc)
    )


def be_time(value):
    if not value:
        return "-"
    return parse_time(value).astimezone(LOCAL).strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def zero(value):
    return number(value) or 0.0


def matches(text, patterns):
    text = str(text or "").lower()
    return any(
        re.search(
            r"(?<![a-z0-9])" + re.escape(term)
            + r"(?![a-z0-9])",
            text,
        )
        for term in patterns
    )


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def select(sql, args=()):
    with db() as c:
        return c.execute(sql, args).fetchall()


def initialize():
    # Old database tables are preserved.
    statements = [
        """CREATE TABLE IF NOT EXISTS discovered (
            address TEXT PRIMARY KEY,
            name TEXT, symbol TEXT,
            first_seen TEXT, last_seen TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS watchlist (
            address TEXT PRIMARY KEY,
            name TEXT, added_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS tracking_state (
            address TEXT PRIMARY KEY,
            first_seen TEXT, last_checked TEXT,
            last_found TEXT,
            missed_scans INTEGER DEFAULT 0,
            status TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS radar_v54 (
            address TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            name TEXT, symbol TEXT, pair_address TEXT,
            source TEXT, price REAL, liquidity REAL,
            volume_m5 REAL, buys_m5 INTEGER,
            sells_m5 INTEGER, age_hours REAL,
            narrative INTEGER, momentum INTEGER,
            evidence TEXT, phase TEXT, warnings TEXT,
            PRIMARY KEY(address,scanned_at)
        )""",

        """CREATE TABLE IF NOT EXISTS paper_v54 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT, pair_address TEXT,
            opened_at TEXT NOT NULL,
            entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL,
            amount_usd REAL NOT NULL,
            quantity REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL,
            status TEXT DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL,
            exit_effective REAL, pnl_usd REAL,
            exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL
        )""",

        """CREATE TABLE IF NOT EXISTS v541_runs (
            scanned_at TEXT PRIMARY KEY,
            finished_at TEXT, fetched INTEGER,
            errors INTEGER, entries INTEGER,
            exits INTEGER, cause TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS v541_decisions (
            scan_at TEXT, address TEXT, name TEXT,
            outcome TEXT, reason TEXT,
            price REAL, liquidity REAL,
            PRIMARY KEY(scan_at,address)
        )""",

        """CREATE TABLE IF NOT EXISTS v541_config (
            name TEXT PRIMARY KEY, value TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS paper_v542_b (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            name TEXT, pair_address TEXT,
            opened_at TEXT NOT NULL,
            entry_market REAL NOT NULL,
            entry_effective REAL NOT NULL,
            amount_usd REAL NOT NULL,
            quantity REAL NOT NULL,
            fee_rate REAL NOT NULL,
            slippage_rate REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            closed_at TEXT, exit_market REAL,
            exit_effective REAL, pnl_usd REAL,
            exit_reason TEXT, last_mark REAL,
            last_mark_at TEXT, peak_mark REAL
        )""",

        """CREATE TABLE IF NOT EXISTS v542_decisions (
            scan_at TEXT NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            strategy TEXT NOT NULL,
            outcome TEXT, reason TEXT,
            price REAL, liquidity REAL,
            PRIMARY KEY(scan_at,address,strategy)
        )""",

        """CREATE TABLE IF NOT EXISTS v542_runs (
            started_at TEXT PRIMARY KEY,
            finished_at TEXT, cause TEXT,
            tokens INTEGER, api_errors INTEGER,
            a_entries INTEGER, b_entries INTEGER,
            a_exits INTEGER, b_exits INTEGER
        )""",

        """CREATE INDEX IF NOT EXISTS idx_v54_history
           ON radar_v54(address,scanned_at)""",

        """CREATE INDEX IF NOT EXISTS idx_v54_paper_status
           ON paper_v54(status)""",

        """CREATE INDEX IF NOT EXISTS idx_v542_paper_status
           ON paper_v542_b(status)""",
    ]

    with db() as c:
        for statement in statements:
            c.execute(statement)


def get_bool(key, default):
    rows = select(
        "SELECT value FROM v541_config WHERE name=?",
        (key,),
    )
    return rows[0][0] == "1" if rows else default


def set_bool(key, state):
    with db() as c:
        c.execute(
            """INSERT INTO v541_config(name,value)
               VALUES(?,?)
               ON CONFLICT(name)
               DO UPDATE SET value=excluded.value""",
            (key, "1" if state else "0"),
        )


# ============================================================
# DEXSCREENER
# ============================================================

def api_get(path, params=None):
    response = HTTP.get(
        API + path, params=params, timeout=20
    )
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
            payload = api_get(
                "/latest/dex/search", {"q": term}
            )

            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue

                base = pair.get("baseToken") or {}
                title = (
                    f"{base.get('name', '')} "
                    f"{base.get('symbol', '')}"
                )

                if matches(title, STRONG + RELATED):
                    add(base.get("address"), "DEX search")

        except (
            requests.RequestException, ValueError, TypeError
        ) as exc:
            errors.append(f"Search {term}: {exc}")

    paths = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for path in paths:
        try:
            payload = api_get(path)

            for item in (
                payload if isinstance(payload, list) else []
            ):
                if item.get("chainId") != "solana":
                    continue

                links = " ".join(
                    str(link.get("url", ""))
                    for link in item.get("links") or []
                    if isinstance(link, dict)
                )

                context = (
                    str(item.get("description") or "")
                    + " " + links
                )

                if matches(context, STRONG + RELATED):
                    add(item.get("tokenAddress"), "DEX profile")

        except (
            requests.RequestException, ValueError, TypeError
        ) as exc:
            errors.append(f"Profile {path}: {exc}")

    historical = [
        "SELECT address FROM paper_v54 WHERE status='OPEN'",
        "SELECT address FROM paper_v542_b WHERE status='OPEN'",
        "SELECT address FROM watchlist ORDER BY added_at DESC",
        """SELECT address FROM tracking_state
           ORDER BY COALESCE(last_checked,first_seen) ASC
           LIMIT 200""",
        """SELECT address FROM discovered
           ORDER BY last_seen ASC LIMIT 200""",
    ]

    for sql in historical:
        for (address,) in select(sql):
            add(address, "Gevolgd")

    return addresses, errors


def market_pairs(addresses):
    results = {}
    fetched = set()
    errors = []

    for offset in range(0, len(addresses), 30):
        batch = addresses[offset:offset + 30]

        try:
            payload = api_get(
                "/tokens/v1/solana/" + ",".join(batch)
            )

            if not isinstance(payload, list):
                raise ValueError("Onverwacht marktdataformaat")

            fetched.update(batch)

            for pair in payload:
                if pair.get("chainId") != "solana":
                    continue

                address = (
                    pair.get("baseToken") or {}
                ).get("address")

                if address not in batch:
                    continue

                current = results.get(address)

                liquidity = zero(
                    (pair.get("liquidity") or {}).get("usd")
                )

                old_liquidity = (
                    zero(
                        (current.get("liquidity") or {})
                        .get("usd")
                    )
                    if current else 0
                )

                if (
                    current is None
                    or liquidity > old_liquidity
                ):
                    results[address] = pair

        except (
            requests.RequestException, ValueError, TypeError
        ) as exc:
            errors.append(
                f"Marktdata batch {offset // 30 + 1}: {exc}"
            )

    return results, fetched, errors


# ============================================================
# MOMENTUM STRATEGY A/B
# ============================================================

def history(address, stamp):
    rows = select(
        """SELECT scanned_at,price,volume_m5,pair_address
           FROM radar_v54
           WHERE address=? AND scanned_at<?
           ORDER BY scanned_at DESC LIMIT 16""",
        (address, stamp),
    )

    return [
        (t, number(p), number(v), pair)
        for t, p, v, pair in rows
    ]


def signals(address, stamp, price, volume, pair):
    if (
        price is None or price <= 0
        or volume is None or volume <= 0
    ):
        return (
            0, False,
            "Prijs of 5m-volume ontbreekt",
            "ONVOLLEDIGE_DATA",
        )

    previous = [
        x for x in history(address, stamp)
        if (
            x[1] is not None and x[1] > 0
            and x[2] is not None and x[2] > 0
            and x[3] == pair
        )
    ]

    if len(previous) < 2:
        return (
            0, False,
            f"{len(previous) + 1}/3 geldige metingen "
            "van dit handelspaar",
            "TE_WEINIG_METINGEN",
        )

    recent, old = previous[0], previous[1]

    gap1 = (
        parse_time(stamp) - parse_time(recent[0])
    ).total_seconds() / 60

    gap2 = (
        parse_time(recent[0]) - parse_time(old[0])
    ).total_seconds() / 60

    span = f"Intervallen {gap2:.1f}m + {gap1:.1f}m"

    if not (
        3.5 <= gap1 <= 20
        and 3.5 <= gap2 <= 20
    ):
        return (
            0, False,
            span + "; meetreeks te onregelmatig",
            "ONGELDIG_INTERVAL",
        )

    rising_price = (
        old[1] < recent[1] < price
        and price >= old[1] * 1.02
    )

    rising_volume = (
        old[2] < recent[2] < volume
        and volume >= 250
    )

    # A: strict price + volume momentum
    a_score = (
        80 if rising_price and rising_volume
        else 30 if rising_price or rising_volume
        else 0
    )

    # B: experimental price momentum
    # Volume needs to be sufficient but not
    # continuously increasing.
    gain = price / old[1] - 1

    b_signal = (
        gain >= 0.05
        and price > recent[1]
        and volume >= 250
    )

    evidence = (
        f"{span}; prijs {gain:+.1%}; "
        f"5m-volume ${volume:,.0f}"
    )

    return a_score, b_signal, evidence, "GEMETEN"


def analyze(addr, pair, origins, stamp):
    base = pair.get("baseToken") or {}

    name = str(base.get("name") or "Onbekend")
    symbol = str(base.get("symbol") or "?")
    title = f"{name} {symbol}"

    narrative = (
        100 if matches(title, STRONG)
        else 65 if matches(title, RELATED)
        else 0
    )

    created = number(pair.get("pairCreatedAt"))

    age = (
        max(
            0.0,
            (
                parse_time(stamp).timestamp() * 1000
                - created
            ) / 3600000,
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

    tx = (pair.get("txns") or {}).get("m5") or {}

    buys = int(zero(tx.get("buys")))
    sells = int(zero(tx.get("sells")))

    pair_id = str(pair.get("pairAddress") or "")

    (
        a_score, b_signal,
        evidence, diagnostic,
    ) = signals(
        addr, stamp, price, volume, pair_id
    )

    transactions = buys + sells

    avg = (
        volume / transactions
        if transactions and volume is not None
        else None
    )

    micro = (
        transactions >= 100
        and avg is not None
        and avg < 3
    )

    warnings = []

    if liquidity is None or liquidity < 10000:
        warnings.append(
            "Liquiditeit onbekend of < $10.000"
        )

    if micro:
        warnings.append(
            "Microtransacties; mogelijke bots"
        )

    warnings.append(
        "Geen controle op mint/freeze, "
        "houders of verkoopbaarheid"
    )

    return {
        "address": addr,
        "name": name,
        "symbol": symbol,
        "pair": pair_id,
        "source": ", ".join(sorted(origins)),
        "narrative": narrative,
        "age": age,
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "micro": micro,
        "avg": avg,
        "a_score": a_score,
        "b_signal": b_signal,
        "evidence": evidence,
        "diagnostic": diagnostic,
        "warnings": "; ".join(warnings),
    }


# ============================================================
# SAVE SCAN HISTORY
# ============================================================

def record_observations(records, stamp):
    with db() as c:
        for r in records:
            c.execute(
                """INSERT INTO discovered(
                    address,name,symbol,first_seen,last_seen
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(address)
                DO UPDATE SET
                    name=excluded.name,
                    symbol=excluded.symbol,
                    last_seen=excluded.last_seen""",
                (
                    r["address"], r["name"],
                    r["symbol"], stamp, stamp,
                ),
            )

            c.execute(
                """INSERT INTO tracking_state(
                    address,first_seen,last_checked,
                    last_found,missed_scans,status
                ) VALUES(?,?,?,?,0,'ACTIEF')
                ON CONFLICT(address)
                DO UPDATE SET
                    last_checked=excluded.last_checked,
                    last_found=excluded.last_found,
                    missed_scans=0,
                    status='ACTIEF'""",
                (
                    r["address"], stamp, stamp, stamp,
                ),
            )

            phase = (
                "MOMENTUM TER CONTROLE"
                if r["a_score"] == 80
                else "PRE-TREND KANDIDAAT"
                if r["age"] is not None and r["age"] < 24
                else "MONITOREN"
            )

            c.execute(
                """INSERT OR REPLACE INTO radar_v54(
                    address,scanned_at,name,symbol,
                    pair_address,source,price,liquidity,
                    volume_m5,buys_m5,sells_m5,age_hours,
                    narrative,momentum,evidence,phase,warnings
                ) VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    r["address"], stamp, r["name"],
                    r["symbol"], r["pair"], r["source"],
                    r["price"], r["liquidity"],
                    r["volume"], r["buys"], r["sells"],
                    r["age"], r["narrative"], r["a_score"],
                    r["evidence"], phase, r["warnings"],
                ),
            )


# ============================================================
# ENTRY DECISIONS
# ============================================================

def decide(r, strategy, cfg, used):
    if r["address"] in used:
        return (
            "GEEN_INSTAP",
            "Deze token werd in deze strategie "
            "al eens virtueel verhandeld",
        )

    if r["narrative"] < 65:
        return "GEEN_INSTAP", "Geen duidelijke GTA-narrative"

    if r["diagnostic"] != "GEMETEN":
        return (
            "GEEN_INSTAP",
            r["diagnostic"] + ": " + r["evidence"],
        )

    if strategy == "A" and r["a_score"] != 80:
        return (
            "GEEN_INSTAP",
            "Streng momentum vereist prijs én "
            "stijgend 5m-volume: " + r["evidence"],
        )

    if strategy == "B" and not r["b_signal"]:
        return (
            "GEEN_INSTAP",
            "Experimenteel momentum vereist +5% prijs, "
            "laatste prijs stijgend en 5m-volume "
            ">= $250: " + r["evidence"],
        )

    if r["price"] is None or r["price"] <= 0:
        return "GEBLOKKEERD", "Prijs ongeldig"

    if (
        r["liquidity"] is None
        or r["liquidity"] < cfg["min_liq"]
    ):
        return (
            "GEBLOKKEERD",
            "Onvoldoende geverifieerde liquiditeit",
        )

    if r["age"] is None or r["age"] > 24:
        return (
            "GEBLOKKEERD",
            "Handelspaar ouder dan 24 uur "
            "of leeftijd onbekend",
        )

    if r["volume"] is None or r["volume"] < 250:
        return (
            "GEBLOKKEERD",
            "Onvoldoende 5m-volume",
        )

    if r["buys"] <= r["sells"]:
        return (
            "GEBLOKKEERD",
            "Kooptransacties niet groter in aantal "
            "dan verkooptransacties",
        )

    if strategy == "A" and r["micro"]:
        return (
            "GEBLOKKEERD",
            "Gemiddelde transactie onder $3 "
            "bij minstens 100 transacties",
        )

    if strategy == "B" and r["micro"]:
        return (
            "KANDIDAAT",
            "EXPERIMENTEEL: microtransacties toegestaan; "
            "contractrisico blijft onbekend",
        )

    return (
        "KANDIDAAT",
        "Kwantitatieve voorwaarden gehaald; "
        "contractveiligheid niet gecontroleerd",
    )


# ============================================================
# A/B PAPER ENGINE
# ============================================================

def paper_run(records, stamp, cfg, active_a, active_b):
    by_address = {
        r["address"]: r
        for r in records
    }

    results = {
        "A": [0, 0],
        "B": [0, 0],
    }

    ledgers = {
        "A": "paper_v54",
        "B": "paper_v542_b",
    }

    for strategy, table in ledgers.items():
        enabled = (
            active_a if strategy == "A"
            else active_b
        )

        with db() as c:
            open_trades = c.execute(
                f"""SELECT id,address,opened_at,
                    entry_market,quantity,amount_usd,
                    fee_rate,slippage_rate
                    FROM {table} WHERE status='OPEN'"""
            ).fetchall()

            used = {
                row[0]
                for row in c.execute(
                    f"SELECT DISTINCT address FROM {table}"
                )
            }

            # --------------------------------------------
            # EXIT EXISTING VIRTUAL POSITIONS
            # --------------------------------------------

            for (
                trade_id, addr, opened, entry,
                quantity, amount, fee, slip,
            ) in open_trades:

                r = by_address.get(addr)

                if not r or not r["price"] or r["price"] <= 0:
                    continue

                price = r["price"]

                c.execute(
                    f"""UPDATE {table}
                        SET last_mark=?,
                            last_mark_at=?,
                            peak_mark=MAX(
                                COALESCE(peak_mark,0),?
                            )
                        WHERE id=?""",
                    (price, stamp, price, trade_id),
                )

                change = price / entry - 1

                hours = (
                    parse_time(stamp)
                    - parse_time(opened)
                ).total_seconds() / 3600

                reason = (
                    "TAKE_PROFIT"
                    if change >= cfg["take"]
                    else "STOP_LOSS"
                    if change <= -cfg["stop"]
                    else "TIME_EXIT"
                    if hours >= cfg["max_hours"]
                    else None
                )

                if not reason:
                    continue

                if (
                    r["liquidity"] is None
                    or r["liquidity"] < cfg["min_liq"]
                ):
                    c.execute(
                        f"""UPDATE {table}
                            SET exit_reason='EXIT_BLOCKED_LIQUIDITY'
                            WHERE id=?""",
                        (trade_id,),
                    )
                    continue

                effective_exit = price * (1 - slip)

                pnl = (
                    quantity
                    * effective_exit
                    * (1 - fee)
                    - amount
                )

                c.execute(
                    f"""UPDATE {table}
                        SET status='CLOSED',
                            closed_at=?,
                            exit_market=?,
                            exit_effective=?,
                            pnl_usd=?,
                            exit_reason=?
                        WHERE id=? AND status='OPEN'""",
                    (
                        stamp, price, effective_exit,
                        pnl, reason, trade_id,
                    ),
                )

                results[strategy][1] += 1

            # --------------------------------------------
            # NEW VIRTUAL ENTRIES
            # --------------------------------------------

            current_open = c.execute(
                f"""SELECT COUNT(*) FROM {table}
                    WHERE status='OPEN'"""
            ).fetchone()[0]

            for r in records:
                outcome, reason = decide(
                    r, strategy, cfg, used
                )

                if outcome == "KANDIDAAT" and not enabled:
                    outcome = "UITGESCHAKELD"
                    reason = (
                        f"Strategie {strategy} paper trading "
                        "staat uit; " + reason
                    )

                elif (
                    outcome == "KANDIDAAT"
                    and current_open >= cfg["max_positions"]
                ):
                    outcome = "GEBLOKKEERD"
                    reason = (
                        "Maximaal "
                        f"{cfg['max_positions']} "
                        "gelijktijdige posities bereikt"
                    )

                elif outcome == "KANDIDAAT":
                    entry_effective = (
                        r["price"] * (1 + cfg["slip"])
                    )

                    qty = (
                        cfg["amount"]
                        * (1 - cfg["fee"])
                        / entry_effective
                    )

                    c.execute(
                        f"""INSERT INTO {table}(
                            address,name,pair_address,
                            opened_at,entry_market,
                            entry_effective,amount_usd,
                            quantity,fee_rate,slippage_rate,
                            status,last_mark,last_mark_at,
                            peak_mark
                        ) VALUES(
                            ?,?,?,?,?,?,?,?,?,?,
                            'OPEN',?,?,?
                        )""",
                        (
                            r["address"], r["name"],
                            r["pair"], stamp,
                            r["price"], entry_effective,
                            cfg["amount"], qty,
                            cfg["fee"], cfg["slip"],
                            r["price"], stamp, r["price"],
                        ),
                    )

                    used.add(r["address"])
                    current_open += 1
                    results[strategy][0] += 1

                    outcome = "PAPER_ENTRY"

                    reason = (
                        "Virtuele order geregistreerd; "
                        "geen controle op daadwerkelijke "
                        "uitvoerbaarheid"
                    )

                c.execute(
                    """INSERT OR REPLACE INTO v542_decisions(
                        scan_at,address,name,strategy,
                        outcome,reason,price,liquidity
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        stamp, r["address"], r["name"],
                        strategy, outcome, reason,
                        r["price"], r["liquidity"],
                    ),
                )

    return results


# ============================================================
# SCANNER
# ============================================================

def scan(trigger, cfg, active_a, active_b):
    start = utcnow()

    addresses, errors = discover()

    pairs, fetched, fetch_errors = market_pairs(
        list(addresses)
    )

    errors.extend(fetch_errors)

    observed = []

    open_addresses = {
        row[0]
        for row in select(
            "SELECT address FROM paper_v54 WHERE status='OPEN'"
        )
    }

    open_addresses |= {
        row[0]
        for row in select(
            "SELECT address FROM paper_v542_b WHERE status='OPEN'"
        )
    }

    for addr, pair in pairs.items():
        try:
            r = analyze(
                addr, pair, addresses[addr], start
            )

            if (
                r["narrative"] >= 65
                or addr in open_addresses
            ):
                observed.append(r)

        except (ValueError, KeyError, TypeError) as exc:
            errors.append(
                f"Analyse {addr[:10]}: {exc}"
            )

    # API errors do not count as delisted tokens.
    record_observations(observed, start)

    results = paper_run(
        observed, start, cfg, active_a, active_b
    )

    end = utcnow()

    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO v542_runs "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                start, end, trigger, len(observed),
                len(errors),
                results["A"][0], results["B"][0],
                results["A"][1], results["B"][1],
            ),
        )

    return len(observed), results, errors


# ============================================================
# DISPLAY DATA
# ============================================================

def latest_tokens():
    row = select(
        """SELECT started_at
           FROM v542_runs
           ORDER BY started_at DESC LIMIT 1"""
    )

    if not row:
        return pd.DataFrame()

    data = select(
        """SELECT name,symbol,address,pair_address,
                  age_hours,price,liquidity,volume_m5,
                  buys_m5,sells_m5,momentum,evidence,warnings
           FROM radar_v54
           WHERE scanned_at=?""",
        (row[0][0],),
    )

    return pd.DataFrame(
        data,
        columns=[
            "Naam", "Symbool", "Adres", "Pair",
            "Leeftijd uur", "Prijs $", "Liquiditeit $",
            "5m-volume $", "Buys", "Sells",
            "A momentum", "Trend", "Waarschuwingen",
        ],
    )


def ledger_view(table):
    rows = select(
        f"""SELECT id,name,address,opened_at,amount_usd,
                   entry_market,last_mark,status,pnl_usd,
                   exit_reason
            FROM {table}
            ORDER BY id DESC LIMIT 300"""
    )

    data = pd.DataFrame(
        rows,
        columns=[
            "ID", "Naam", "Adres", "Open UTC",
            "Inzet $", "Instap $", "Laatste prijs $",
            "Status", "Gerealiseerd P/L $", "Exitreden",
        ],
    )

    if not data.empty:
        data["Geopend België"] = (
            data["Open UTC"].apply(be_time)
        )
        data = data.drop(columns=["Open UTC"])

    return data


# ============================================================
# STREAMLIT APPLICATION
# ============================================================

initialize()

st.title("🎮 GTA 6 Radar V5.4.2")

st.caption(
    "A/B-paperstrategieën · Belgische scanplanning · "
    "bestaande V5.4 database behouden"
)

st.warning(
    "Uitsluitend gesimuleerde transacties. "
    "Een koers of stoploss uit marktdata betekent niet "
    "dat een echte koop/verkoop uitvoerbaar zou zijn. "
    "Mint/freeze, walletconcentratie en honeypots "
    "worden niet gecontroleerd."
)

if "v542_auto" not in st.session_state:
    st.session_state.v542_auto = get_bool(
        "auto_scan", True
    )

if "v542_a" not in st.session_state:
    st.session_state.v542_a = get_bool(
        "paper", False
    )

if "v542_b" not in st.session_state:
    st.session_state.v542_b = get_bool(
        "paper_b", False
    )


def change_auto():
    set_bool(
        "auto_scan", st.session_state.v542_auto
    )


def change_a():
    set_bool(
        "paper", st.session_state.v542_a
    )


def change_b():
    set_bool(
        "paper_b", st.session_state.v542_b
    )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Scanner")

st.sidebar.toggle(
    "Automatisch scannen (actieve sessie)",
    key="v542_auto",
    on_change=change_auto,
)

st.sidebar.toggle(
    "Strategie A: paperinstappen",
    key="v542_a",
    on_change=change_a,
)

st.sidebar.toggle(
    "Strategie B: paperinstappen",
    key="v542_b",
    on_change=change_b,
)

interval = st.sidebar.selectbox(
    "Scaninterval (min)", [5, 10, 15], index=0
)

st.sidebar.caption(
    "A = stijgende prijs én 5m-volume + "
    "microtransactiefilter. "
    "B = minstens +5% prijsstijging over drie "
    "meetpunten, recent stijgende prijs en minimumvolume. "
    "Microtransacties worden gelogd, maar niet "
    "automatisch geblokkeerd."
)

minimum_liq = st.sidebar.number_input(
    "Minimale liquiditeit ($)",
    min_value=1000.0,
    max_value=1000000.0,
    value=10000.0,
    step=1000.0,
)

amount = st.sidebar.number_input(
    "Virtuele inzet per positie ($)",
    min_value=5.0,
    max_value=1000.0,
    value=25.0,
    step=5.0,
)

max_positions = st.sidebar.number_input(
    "Max. open posities per strategie",
    min_value=1,
    max_value=20,
    value=3,
)

fee_pct = st.sidebar.number_input(
    "Fee per zijde (%)", 0.0, 10.0, 1.0, 0.25
)

slip_pct = st.sidebar.number_input(
    "Slippage per zijde (%)", 0.0, 30.0, 2.0, 0.5
)

take_pct = st.sidebar.number_input(
    "Take profit (%)", 5, 500, 30, 5
)

stop_pct = st.sidebar.number_input(
    "Stop loss (%)", 5, 95, 15, 5
)

max_hours = st.sidebar.number_input(
    "Max. looptijd (uur)", 1, 72, 4
)

settings = {
    "min_liq": minimum_liq,
    "amount": amount,
    "max_positions": max_positions,
    "fee": fee_pct / 100,
    "slip": slip_pct / 100,
    "take": take_pct / 100,
    "stop": stop_pct / 100,
    "max_hours": max_hours,
}


# ============================================================
# LIVE DASHBOARD
# ============================================================

@st.fragment(run_every="60s")
def dashboard():
    manual = st.button(
        "🔄 Scan nu", type="primary"
    )

    last = select(
        """SELECT started_at,finished_at,cause
           FROM v542_runs
           ORDER BY started_at DESC LIMIT 1"""
    )

    last_end = (
        parse_time(last[0][1])
        if last and last[0][1]
        else None
    )

    current = datetime.now(timezone.utc)

    next_scan = (
        last_end + timedelta(minutes=interval)
        if last_end else current
    )

    st.subheader("🕒 Scanplanning — Belgische tijd")

    st.write(
        "Automatisch scannen:",
        "**AAN**"
        if st.session_state.v542_auto
        else "**UIT**",
    )

    st.write(
        "Laatste scan:",
        (
            f"**{be_time(last[0][1])}** ({last[0][2]})"
            if last
            else "Nog geen scan in V5.4.2"
        ),
    )

    st.write(
        "Volgende scan vanaf:",
        (
            f"**{be_time(next_scan.isoformat())}**"
            if st.session_state.v542_auto
            else "Niet ingepland"
        ),
    )

    st.caption(
        "De pagina controleert ongeveer elke minuut "
        "of een scan verschuldigd is. Een slapende "
        "tab/server of lange API-call kan het interval "
        "vertragen. Geen gegarandeerde 24/7-werking."
    )

    due = (
        st.session_state.v542_auto
        and current >= next_scan
    )

    if manual or due:
        try:
            with st.spinner(
                "Solana-tokens scannen en "
                "beide strategieën evalueren..."
            ):
                count, result, errors = scan(
                    "MANUEEL" if manual else "AUTOMATISCH",
                    settings,
                    st.session_state.v542_a,
                    st.session_state.v542_b,
                )

            st.success(
                f"Scan klaar: {count} tokens · "
                f"A: {result['A'][0]} instappen · "
                f"B: {result['B'][0]} instappen"
            )

            if errors:
                with st.expander(
                    f"{len(errors)} meldingen over API/data"
                ):
                    st.write(errors[:35])

        except (
            sqlite3.Error,
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            st.error(f"Scanfout: {exc}")

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    tokens = latest_tokens()

    a_open = select(
        """SELECT COUNT(*) FROM paper_v54
           WHERE status='OPEN'"""
    )[0][0]

    b_open = select(
        """SELECT COUNT(*) FROM paper_v542_b
           WHERE status='OPEN'"""
    )[0][0]

    a, b, c, d = st.columns(4)

    a.metric("Tokens laatste scan", len(tokens))

    b.metric(
        "A: momentum 80",
        int((tokens["A momentum"] == 80).sum())
        if not tokens.empty else 0,
    )

    c.metric("A: open posities", a_open)
    d.metric("B: open posities", b_open)

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    st.subheader("📡 Laatste tokenmeting")

    if tokens.empty:
        st.info(
            "Nog geen V5.4.2-scan opgeslagen. "
            "Gebruik Scan nu of houd de pagina actief."
        )

    else:
        st.dataframe(
            tokens.sort_values(
                ["5m-volume $", "A momentum"],
                ascending=False,
                na_position="last",
            ),
            hide_index=True,
            use_container_width=True,
        )

        st.download_button(
            "📥 Tokenmetingen CSV",
            tokens.to_csv(index=False).encode("utf-8"),
            "gta6_v542_tokens.csv",
            "text/csv",
        )

        choices = {
            f"{r['Naam']} · {r['Adres'][:12]}": r["Adres"]
            for _, r in tokens.iterrows()
        }

        chosen = st.selectbox(
            "Tokenhistoriek", list(choices)
        )

        address = choices[chosen]

        one = tokens[
            tokens["Adres"] == address
        ].iloc[0]

        st.code(address, language="text")

        st.link_button(
            "Bekijk DexScreener",
            "https://dexscreener.com/solana/"
            + str(one["Pair"] or address),
        )

        if st.button("⭐ Voeg toe aan watchlist"):
            with db() as c:
                c.execute(
                    """INSERT OR IGNORE INTO watchlist(
                        address,name,added_at
                    ) VALUES(?,?,?)""",
                    (address, one["Naam"], utcnow()),
                )

            st.success("Toegevoegd")

        hist = select(
            """SELECT scanned_at,price,volume_m5
               FROM radar_v54
               WHERE address=?
               ORDER BY scanned_at DESC LIMIT 120""",
            (address,),
        )

        if len(hist) >= 2:
            hd = pd.DataFrame(
                hist,
                columns=[
                    "Tijd", "Prijs $", "5m-volume $",
                ],
            )

            hd["Tijd"] = pd.to_datetime(
                hd["Tijd"], utc=True
            ).dt.tz_convert("Europe/Brussels")

            hd = hd.sort_values("Tijd").set_index("Tijd")

            st.line_chart(hd[["Prijs $"]])
            st.line_chart(hd[["5m-volume $"]])

    # --------------------------------------------------------
    # A/B DECISION LOG
    # --------------------------------------------------------

    st.subheader("🔎 Vergelijking beslissingen A en B")

    newest = select(
        """SELECT started_at FROM v542_runs
           ORDER BY started_at DESC LIMIT 1"""
    )

    if newest:
        decision_rows = select(
            """SELECT name,address,strategy,
                      outcome,reason,price,liquidity
               FROM v542_decisions
               WHERE scan_at=?
               ORDER BY strategy,outcome,name""",
            (newest[0][0],),
        )

        decisions = pd.DataFrame(
            decision_rows,
            columns=[
                "Naam", "Adres", "Strategie",
                "Uitkomst", "Reden", "Prijs $",
                "Liquiditeit $",
            ],
        )

        filter_strategy = st.radio(
            "Toon strategie",
            ["Allebei", "A", "B"],
            horizontal=True,
        )

        view = (
            decisions if filter_strategy == "Allebei"
            else decisions[
                decisions["Strategie"] == filter_strategy
            ]
        )

        st.dataframe(
            view,
            hide_index=True,
            use_container_width=True,
        )

        st.download_button(
            "📥 A/B-beslissingen CSV",
            decisions.to_csv(index=False).encode("utf-8"),
            "gta6_v542_ab_beslissingen.csv",
            "text/csv",
        )

    # --------------------------------------------------------
    # PAPER PORTFOLIOS
    # --------------------------------------------------------

    st.subheader(
        "🧪 Paperportfolio’s (volledig gescheiden)"
    )

    ta, tb = st.tabs([
        "Strategie A — streng",
        "Strategie B — experimenteel",
    ])

    for tab, table in [
        (ta, "paper_v54"),
        (tb, "paper_v542_b"),
    ]:
        with tab:
            trades = ledger_view(table)

            if trades.empty:
                st.info(
                    "Nog geen virtuele posities "
                    "in deze strategie."
                )

            else:
                closed = trades[
                    trades["Status"] == "CLOSED"
                ]

                cols = st.columns(3)

                cols[0].metric(
                    "Totaal geregistreerde posities",
                    len(trades),
                )

                cols[1].metric(
                    "Gerealiseerd P/L",
                    "$"
                    f"{closed['Gerealiseerd P/L $'].sum():,.2f}",
                )

                cols[2].metric(
                    "Winrate gesloten",
                    (
                        f"{(closed['Gerealiseerd P/L $'] > 0).mean() * 100:.1f}%"
                        if not closed.empty else "-"
                    ),
                )

                st.dataframe(
                    trades,
                    hide_index=True,
                    use_container_width=True,
                )

                st.download_button(
                    f"📥 Trades {table} CSV",
                    trades.to_csv(
                        index=False
                    ).encode("utf-8"),
                    f"{table}.csv",
                    "text/csv",
                )

    # --------------------------------------------------------
    # SCAN LOG
    # --------------------------------------------------------

    st.subheader("🕒 Scanlog (Belgische tijd)")

    logs = select(
        """SELECT started_at,finished_at,cause,
                  tokens,api_errors,a_entries,b_entries,
                  a_exits,b_exits
           FROM v542_runs
           ORDER BY started_at DESC LIMIT 40"""
    )

    if logs:
        ld = pd.DataFrame(
            logs,
            columns=[
                "Begin UTC", "Einde UTC",
                "Trigger", "Tokens", "API-meldingen",
                "A instappen", "B instappen",
                "A exits", "B exits",
            ],
        )

        ld["Begin België"] = (
            ld["Begin UTC"].apply(be_time)
        )

        ld["Einde België"] = (
            ld["Einde UTC"].apply(be_time)
        )

        st.dataframe(
            ld.drop(
                columns=["Begin UTC", "Einde UTC"]
            ),
            hide_index=True,
            use_container_width=True,
        )


dashboard()

st.caption(
    "Simulatie gebruikt gesamplede marktprijzen, "
    "schattingen voor fees/slippage en veronderstelde fills. "
    "Resultaten zijn geen bewezen rendement of "
    "gegarandeerde verkoopbaarheid. SQLite op tijdelijke "
    "hosting kan verloren gaan zonder persistent volume/back-up."
)
