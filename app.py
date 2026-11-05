
"""GTA 6 Radar V5.4.1 - Streamlit. Research and simulated orders only."""
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="GTA 6 Radar V5.4.1", page_icon="🎮", layout="wide")
DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
API = "https://api.dexscreener.com"
SEARCHES = ["GTA6", "GTA VI", "Grand Theft Auto", "Vice City", "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn"]
STRONG = ["gta6", "gta 6", "gta vi", "grand theft auto", "grandtheftauto", "gtavi"]
RELATED = ["vice city", "leonida", "lucia camino", "jason duval", "port gellhorn", "raul bautista"]
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "GTA6ResearchRadar/5.4.1", "Accept": "application/json"})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dt(value):
    return datetime.fromisoformat(value)


def num(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def n(value):
    return num(value) or 0.0


def match(value, terms):
    text = str(value or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text) for t in terms)


def conn():
    c = sqlite3.connect(DB, timeout=25)
    c.execute("PRAGMA busy_timeout=25000")
    return c


def query(sql, args=()):
    with conn() as c:
        return c.execute(sql, args).fetchall()


def setup():
    tables = [
        "CREATE TABLE IF NOT EXISTS discovered (address TEXT PRIMARY KEY, name TEXT, symbol TEXT, first_seen TEXT, last_seen TEXT)",
        "CREATE TABLE IF NOT EXISTS watchlist (address TEXT PRIMARY KEY, name TEXT, added_at TEXT)",
        "CREATE TABLE IF NOT EXISTS tracking_state (address TEXT PRIMARY KEY, first_seen TEXT, last_checked TEXT, last_found TEXT, missed_scans INTEGER DEFAULT 0, status TEXT)",
        """CREATE TABLE IF NOT EXISTS radar_v54 (address TEXT NOT NULL, scanned_at TEXT NOT NULL,
        name TEXT, symbol TEXT, pair_address TEXT, source TEXT, price REAL, liquidity REAL,
        volume_m5 REAL, buys_m5 INTEGER, sells_m5 INTEGER, age_hours REAL, narrative INTEGER,
        momentum INTEGER, evidence TEXT, phase TEXT, warnings TEXT,
        PRIMARY KEY(address, scanned_at))""",
        """CREATE TABLE IF NOT EXISTS paper_v54 (id INTEGER PRIMARY KEY AUTOINCREMENT,
        address TEXT NOT NULL, name TEXT, pair_address TEXT, opened_at TEXT NOT NULL,
        entry_market REAL NOT NULL, entry_effective REAL NOT NULL, amount_usd REAL NOT NULL,
        quantity REAL NOT NULL, fee_rate REAL NOT NULL, slippage_rate REAL NOT NULL,
        status TEXT DEFAULT 'OPEN', closed_at TEXT, exit_market REAL, exit_effective REAL,
        pnl_usd REAL, exit_reason TEXT, last_mark REAL, last_mark_at TEXT, peak_mark REAL)""",
        """CREATE TABLE IF NOT EXISTS v541_runs (scanned_at TEXT PRIMARY KEY, finished_at TEXT,
        fetched INTEGER, errors INTEGER, entries INTEGER, exits INTEGER, cause TEXT)""",
        """CREATE TABLE IF NOT EXISTS v541_decisions (scan_at TEXT, address TEXT, name TEXT,
        outcome TEXT, reason TEXT, price REAL, liquidity REAL,
        PRIMARY KEY(scan_at,address))""",
        "CREATE TABLE IF NOT EXISTS v541_config (name TEXT PRIMARY KEY, value TEXT)",
        "CREATE INDEX IF NOT EXISTS ix_v54_history ON radar_v54(address, scanned_at)",
        "CREATE INDEX IF NOT EXISTS ix_paper_v54_status ON paper_v54(status)",
    ]
    with conn() as c:
        for statement in tables:
            c.execute(statement)


def config_get(key, default):
    r = query("SELECT value FROM v541_config WHERE name=?", (key,))
    return r[0][0] == "1" if r else default


def config_set(key, value):
    with conn() as c:
        c.execute("INSERT INTO v541_config(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (key, "1" if value else "0"))


def fetch(url, params=None):
    r = HTTP.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def discovery():
    found, errors = {}, []

    def add(address, source):
        if isinstance(address, str) and ADDRESS.fullmatch(address):
            found.setdefault(address, set()).add(source)

    for word in SEARCHES:
        try:
            data = fetch(API + "/latest/dex/search", {"q": word})
            for pair in data.get("pairs") or []:
                base = pair.get("baseToken") or {}
                if pair.get("chainId") == "solana" and match(str(base.get("name", "")) + " " + str(base.get("symbol", "")), STRONG + RELATED):
                    add(base.get("address"), "DEX search")
        except (requests.RequestException, ValueError, TypeError) as e:
            errors.append(f"Search {word}: {e}")

    for endpoint in ["/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"]:
        try:
            data = fetch(API + endpoint)
            for item in data if isinstance(data, list) else []:
                desc = str(item.get("description") or "") + " " + " ".join(str(v.get("url", "")) for v in item.get("links") or [])
                if item.get("chainId") == "solana" and match(desc, STRONG + RELATED):
                    add(item.get("tokenAddress"), "DEX profile")
        except (requests.RequestException, ValueError, TypeError) as e:
            errors.append(f"Profile {endpoint}: {e}")

    primary = [r[0] for r in query("SELECT address FROM paper_v54 WHERE status='OPEN'")]
    primary += [r[0] for r in query("SELECT address FROM watchlist ORDER BY added_at DESC")]
    primary += [r[0] for r in query("SELECT address FROM tracking_state ORDER BY COALESCE(last_checked, first_seen) ASC LIMIT 250")]
    primary += [r[0] for r in query("SELECT address FROM discovered ORDER BY last_seen ASC LIMIT 250")]

    for a in primary:
        if isinstance(a, str) and ADDRESS.fullmatch(a):
            found.setdefault(a, set()).add("Historisch gevolgd")
    return found, errors


def prices(addresses):
    best, successful, errors = {}, set(), []
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        try:
            data = fetch(API + "/tokens/v1/solana/" + ",".join(batch))
            if not isinstance(data, list):
                raise ValueError("Onverwacht antwoord")
            successful.update(batch)
            for pair in data:
                if pair.get("chainId") != "solana":
                    continue
                address = (pair.get("baseToken") or {}).get("address")
                if address not in batch:
                    continue
                old = best.get(address)
                liquidity = n((pair.get("liquidity") or {}).get("usd"))
                if old is None or liquidity > n((old.get("liquidity") or {}).get("usd")):
                    best[address] = pair
        except (requests.RequestException, ValueError, TypeError) as e:
            errors.append(f"Market batch {i // 30 + 1}: {e}")
    return best, successful, errors


def trend(address, stamp, price, volume):
    older = query("""SELECT scanned_at,price,volume_m5,pair_address FROM radar_v54
                     WHERE address=? AND scanned_at<? ORDER BY scanned_at DESC LIMIT 12""", (address, stamp))
    good = [(t, p, v, pair) for t, p, v, pair in older if n(p) > 0 and n(v) > 0]
    if price is None or volume is None or price <= 0 or volume <= 0:
        return 0, "Prijs of 5m-volume ontbreekt", "ONVOLLEDIGE_DATA", None
    if len(good) < 2:
        return 0, f"{len(good) + 1}/3 geldige metingen", "TE_WEINIG_METINGEN", None

    newest, oldest = good[0], good[1]
    gap_a = (dt(stamp) - dt(newest[0])).total_seconds() / 60
    gap_b = (dt(newest[0]) - dt(oldest[0])).total_seconds() / 60
    timing = f"Tussenpozen {gap_b:.1f}m en {gap_a:.1f}m"
    if not (3.5 <= gap_a <= 20 and 3.5 <= gap_b <= 20):
        return 0, timing + "; nieuwe meetreeks nodig", "ONGELDIG_INTERVAL", (gap_b, gap_a)
    rising_price = oldest[1] < newest[1] < price and price >= oldest[1] * 1.02
    rising_volume = oldest[2] < newest[2] < volume and volume >= 250
    if rising_price and rising_volume:
        return 80, timing + "; prijs en gemeten 5m-volume stijgen", "MOMENTUM", (gap_b, gap_a)
    if rising_price or rising_volume:
        return 30, timing + "; slechts een trendvoorwaarde", "GEDEELTELIJK", (gap_b, gap_a)
    return 0, timing + "; geen opwaartse trend", "GEEN_TREND", (gap_b, gap_a)


def assess(address, pair, sources, stamp):
    token = pair.get("baseToken") or {}
    name = str(token.get("name") or "Unknown")
    symbol = str(token.get("symbol") or "?")
    title = name + " " + symbol
    narrative = 100 if match(title, STRONG) else 65 if match(title, RELATED) else 0
    created = num(pair.get("pairCreatedAt"))
    age = max(0, (dt(stamp).timestamp() * 1000 - created) / 3600000) if created else None
    price = num(pair.get("priceUsd"))
    liq = num((pair.get("liquidity") or {}).get("usd"))
    vol = num((pair.get("volume") or {}).get("m5"))
    tx = (pair.get("txns") or {}).get("m5") or {}
    buys, sells = int(n(tx.get("buys"))), int(n(tx.get("sells")))
    score, evidence, diagnostic, gaps = trend(address, stamp, price, vol)
    warnings = []
    if liq is None:
        warnings.append("Liquiditeit onbekend")
    elif liq < 10000:
        warnings.append("Liquiditeit onder $10.000")
    if buys + sells >= 100 and vol is not None and vol / (buys + sells) < 3:
        warnings.append("Kleine gemiddelde transactie; bots mogelijk")
    warnings.append("Holders, mint/freeze en verkoopbaarheid niet geverifieerd")
    phase = "MOMENTUM TER CONTROLE" if score == 80 and n(liq) >= 10000 and narrative >= 65 else ("PRE-TREND KANDIDAAT" if age is not None and age < 24 else "MONITOREN")
    return dict(address=address, name=name, symbol=symbol, sources=", ".join(sorted(sources)),
                narrative=narrative, age=age, price=price, liquidity=liq, volume=vol,
                buys=buys, sells=sells, momentum=score, evidence=evidence, diagnostic=diagnostic,
                gaps=gaps, phase=phase, warnings="; ".join(warnings), pair=pair.get("pairAddress") or "")


def save_observations(records, missing, stamp):
    with conn() as c:
        for r in records:
            c.execute("""INSERT INTO discovered(address,name,symbol,first_seen,last_seen) VALUES(?,?,?,?,?)
                ON CONFLICT(address) DO UPDATE SET name=excluded.name,symbol=excluded.symbol,last_seen=excluded.last_seen""",
                (r["address"], r["name"], r["symbol"], stamp, stamp))
            c.execute("""INSERT INTO tracking_state(address,first_seen,last_checked,last_found,missed_scans,status)
                VALUES(?,?,?,?,0,'ACTIEF') ON CONFLICT(address) DO UPDATE SET last_checked=excluded.last_checked,
                last_found=excluded.last_found,missed_scans=0,status='ACTIEF'""", (r["address"], stamp, stamp, stamp))
            c.execute("""INSERT OR REPLACE INTO radar_v54(address,scanned_at,name,symbol,pair_address,source,
                price,liquidity,volume_m5,buys_m5,sells_m5,age_hours,narrative,momentum,evidence,phase,warnings)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["address"], stamp, r["name"], r["symbol"], r["pair"], r["sources"], r["price"],
                 r["liquidity"], r["volume"], r["buys"], r["sells"], r["age"], r["narrative"],
                 r["momentum"], r["evidence"], r["phase"], r["warnings"]))
        for a in missing:
            c.execute("UPDATE tracking_state SET last_checked=?, missed_scans=missed_scans+1,status='NIET_GEVONDEN' WHERE address=?", (stamp, a))


def decide(r, cfg, already):
    if r["address"] in already:
        return "GEEN_INSTAP", "Al eerder virtueel verhandeld"
    if r["narrative"] < 65:
        return "GEEN_INSTAP", "Geen duidelijke GTA-narrative"
    if r["momentum"] != 80:
        return "GEEN_INSTAP", r["diagnostic"] + ": " + r["evidence"]
    if r["price"] is None or r["price"] <= 0:
        return "GEBLOKKEERD", "Ongeldige prijs"
    if r["liquidity"] is None or r["liquidity"] < cfg["minimum"]:
        return "GEBLOKKEERD", "Liquiditeit ontbreekt of is te laag"
    if r["age"] is None or r["age"] > 24:
        return "GEBLOKKEERD", "Handelspaar ouder dan 24u of leeftijd onbekend"
    if r["volume"] is None or r["volume"] < 250:
        return "GEBLOKKEERD", "Onvoldoende 5m-volume"
    total = r["buys"] + r["sells"]
    if total >= 100 and r["volume"] / total < 3:
        return "GEBLOKKEERD", "Microtransacties: gemiddeld onder $3"
    if r["buys"] <= r["sells"]:
        return "GEBLOKKEERD", "Niet meer koop- dan verkooptransacties"
    return "KANDIDAAT", "Momentum en kwantitatieve filters voldaan; contractrisico onbekend"


def paper_engine(records, stamp, enabled, cfg):
    found = {r["address"]: r for r in records}
    entries, exits = 0, 0
    with conn() as c:
        trades = c.execute("""SELECT id,address,opened_at,entry_market,quantity,amount_usd,fee_rate,slippage_rate
            FROM paper_v54 WHERE status='OPEN'""").fetchall()
        used = {r[0] for r in c.execute("SELECT DISTINCT address FROM paper_v54").fetchall()}
        for tid, address, opened, entry, qty, amount, fee, slip in trades:
            r = found.get(address)
            if not r or not r["price"] or r["price"] <= 0:
                continue
            price = r["price"]
            c.execute("UPDATE paper_v54 SET last_mark=?,last_mark_at=?,peak_mark=MAX(COALESCE(peak_mark,0),?) WHERE id=?",
                      (price, stamp, price, tid))
            change = price / entry - 1
            elapsed = (dt(stamp) - dt(opened)).total_seconds() / 3600
            reason = "TAKE_PROFIT" if change >= cfg["take"] else "STOP_LOSS" if change <= -cfg["stop"] else "TIME_EXIT" if elapsed >= cfg["hours"] else None
            if not reason:
                continue
            if r["liquidity"] is None or r["liquidity"] < cfg["minimum"]:
                c.execute("UPDATE paper_v54 SET exit_reason='EXIT_BLOCKED_LIQUIDITY' WHERE id=?", (tid,))
                continue
            effective = price * (1 - slip)
            pnl = qty * effective * (1 - fee) - amount
            c.execute("""UPDATE paper_v54 SET status='CLOSED',closed_at=?,exit_market=?,exit_effective=?,
                pnl_usd=?,exit_reason=? WHERE id=? AND status='OPEN'""",
                      (stamp, price, effective, pnl, reason, tid))
            exits += 1
        for r in records:
            outcome, explanation = decide(r, cfg, used)
            if outcome == "KANDIDAAT" and not enabled:
                outcome, explanation = "UITGESCHAKELD", "Automatische PAPER trading staat uit"
            if outcome == "KANDIDAAT" and enabled:
                effective = r["price"] * (1 + cfg["slip"])
                quantity = cfg["amount"] * (1 - cfg["fee"]) / effective
                c.execute("""INSERT INTO paper_v54(address,name,pair_address,opened_at,entry_market,
                    entry_effective,amount_usd,quantity,fee_rate,slippage_rate,status,last_mark,last_mark_at,peak_mark)
                    VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?)""",
                    (r["address"], r["name"], r["pair"], stamp, r["price"], effective, cfg["amount"],
                     quantity, cfg["fee"], cfg["slip"], r["price"], stamp, r["price"]))
                used.add(r["address"])
                entries += 1
                outcome, explanation = "PAPER_ENTRY", "Virtuele instap geregistreerd; geen blockchainorder"
            c.execute("""INSERT OR REPLACE INTO v541_decisions(scan_at,address,name,outcome,reason,price,liquidity)
                VALUES(?,?,?,?,?,?,?)""",
                (stamp, r["address"], r["name"], outcome, explanation, r["price"], r["liquidity"]))
    return entries, exits


def run_scan(cause, enabled, cfg):
    stamp = now()
    found, errors = discovery()
    market, success, problems = prices(list(found))
    errors.extend(problems)
    observations = []
    open_addresses = {x[0] for x in query("SELECT address FROM paper_v54 WHERE status='OPEN'")}
    for address, pair in market.items():
        try:
            r = assess(address, pair, found[address], stamp)
            if r["narrative"] >= 65 or address in open_addresses:
                observations.append(r)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"Analyse {address[:8]}: {exc}")
    missing = [a for a in success - set(market) if query("SELECT 1 FROM tracking_state WHERE address=?", (a,))]
    save_observations(observations, missing, stamp)
    entries, exits = paper_engine(observations, stamp, enabled, cfg)
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO v541_runs VALUES(?,?,?,?,?,?,?)",
                  (stamp, now(), len(observations), len(errors), entries, exits, cause))
    return pd.DataFrame(observations), errors, stamp, entries, exits


def recent_frame():
    last = query("SELECT scanned_at FROM v541_runs ORDER BY scanned_at DESC LIMIT 1")
    if not last:
        return pd.DataFrame()
    data = query("""SELECT address,name,symbol,source,narrative,age_hours,price,liquidity,
        volume_m5,buys_m5,sells_m5,momentum,evidence,phase,warnings,pair_address
        FROM radar_v54 WHERE scanned_at=?""", (last[0][0],))
    labels = ["address", "name", "symbol", "sources", "narrative", "age", "price", "liquidity",
              "volume", "buys", "sells", "momentum", "evidence", "phase", "warnings", "pair"]
    return pd.DataFrame(data, columns=labels)


setup()
st.title("🎮 GTA 6 Radar V5.4.1")
st.caption("Persistent Tracking | Flexibele tijdvensters | PAPER Trading | Beslissingslogboek")
st.warning("Geen echte orders. Marktprijzen zijn geen gegarandeerde uitvoerbare transacties. Contractveiligheid en verkoopbaarheid zijn niet gecontroleerd.")

st.sidebar.header("Instellingen")
if "v541_auto" not in st.session_state:
    st.session_state.v541_auto = config_get("auto_scan", True)
if "v541_paper" not in st.session_state:
    st.session_state.v541_paper = config_get("paper", False)


def update_auto():
    config_set("auto_scan", st.session_state.v541_auto)


def update_paper():
    config_set("paper", st.session_state.v541_paper)


st.sidebar.toggle("Automatisch scannen (actieve sessie)", key="v541_auto", on_change=update_auto)
st.sidebar.toggle("Automatische PAPER trading", key="v541_paper", on_change=update_paper)
interval = st.sidebar.selectbox("Scaninterval", [5, 10, 15], index=0)
minimum = st.sidebar.number_input("Min. liquiditeit ($)", 1000.0, 1000000.0, 10000.0, 1000.0)
amount = st.sidebar.number_input("Virtuele inzet ($)", 5.0, 1000.0, 25.0, 5.0)
fee = st.sidebar.number_input("Fee per zijde (%)", 0.0, 10.0, 1.0, 0.25)
slip = st.sidebar.number_input("Slippage per zijde (%)", 0.0, 30.0, 2.0, 0.5)
take = st.sidebar.number_input("Take profit (%)", 5, 500, 30, 5)
stop = st.sidebar.number_input("Stop loss (%)", 5, 95, 15, 5)
hours = st.sidebar.number_input("Max. looptijd (uur)", 1, 72, 4, 1)
config = dict(minimum=minimum, amount=amount, fee=fee / 100, slip=slip / 100,
              take=take / 100, stop=stop / 100, hours=float(hours))
st.sidebar.caption("Auto-scan heeft een actief Streamlit-tabblad nodig. Geen 24/7 garantie. De SQLite-database op Community Cloud is niet permanent gegarandeerd.")

if "v541_scan_errors" not in st.session_state:
    st.session_state.v541_scan_errors = []


@st.fragment(run_every="60s")
def dashboard():
    manual = st.button("🔄 Scan nu", type="primary")
    last = query("SELECT scanned_at,finished_at,cause FROM v541_runs ORDER BY scanned_at DESC LIMIT 1")
    finished = dt(last[0][1]) if last else None
    next_due = finished + timedelta(minutes=interval) if finished else datetime.now(timezone.utc)
    current = datetime.now(timezone.utc)
    due = st.session_state.v541_auto and current >= next_due

    st.caption("Automatisch scannen: " + ("AAN" if st.session_state.v541_auto else "UIT") +
               " | Automatische PAPER trading: " + ("AAN" if st.session_state.v541_paper else "UIT"))
    if last:
        st.write(f"Laatste voltooide scan: **{last[0][1]} UTC** ({last[0][2]})")
    st.write(f"Volgende scan vanaf: **{next_due.strftime('%H:%M:%S')} UTC** " +
             ("(sessie actief houden)" if st.session_state.v541_auto else "(automatisch scannen uit)"))

    if manual or due:
        with st.spinner("Scannen; dit kan even duren..."):
            try:
                data, errs, timestamp, entries, exits = run_scan(
                    "MANUEEL" if manual else "AUTOMATISCH", st.session_state.v541_paper, config)
                st.session_state.v541_scan_errors = errs
                st.success(f"Scan {timestamp}: {len(data)} tokens | {entries} virtuele instappen | {exits} uitstappen")
            except (requests.RequestException, sqlite3.Error, ValueError, TypeError) as exc:
                st.error(f"Scan mislukt: {exc}")

    df = recent_frame()
    total = query("SELECT COUNT(*) FROM discovered")[0][0]
    opened = query("SELECT COUNT(*) FROM paper_v54 WHERE status='OPEN'")[0][0]
    a, b, c, d = st.columns(4)
    a.metric("Tokens laatste scan", len(df))
    b.metric("Momentum 80", int((df["momentum"] == 80).sum()) if not df.empty else 0)
    c.metric("Historisch opgeslagen", total)
    d.metric("Open paper posities", opened)

    if not df.empty:
        st.subheader("📈 Momentum en nieuwe tokens")
        early = df[df["age"].notna() & (df["age"] < 24)]
        st.dataframe(early.sort_values("age"), hide_index=True, use_container_width=True)

        st.subheader("Alle GTA-tokens")
        st.dataframe(df.sort_values(["momentum", "volume"], ascending=False), hide_index=True, use_container_width=True)

        addresses = {f"{r['name']} | {r['address'][:10]}": r["address"] for _, r in df.iterrows()}
        selected = addresses[st.selectbox("Bekijk token", list(addresses))]
        item = df[df["address"] == selected].iloc[0]
        st.code(selected)
        st.write("**Trend:** " + str(item["evidence"]))
        st.write("**Risico:** " + str(item["warnings"]))
        st.link_button("Open DexScreener", "https://dexscreener.com/solana/" + (item["pair"] or selected))

        if st.button("⭐ Voeg toe aan watchlist"):
            with conn() as c:
                c.execute("INSERT OR IGNORE INTO watchlist VALUES(?,?,?)", (selected, item["name"], now()))
            st.success("Toegevoegd")

        history = query("SELECT scanned_at,price,volume_m5 FROM radar_v54 WHERE address=? ORDER BY scanned_at DESC LIMIT 100", (selected,))
        if len(history) >= 2:
            hist = pd.DataFrame(history, columns=["time", "price", "volume"])
            hist["time"] = pd.to_datetime(hist["time"], utc=True)
            hist = hist.sort_values("time").set_index("time")
            st.line_chart(hist[["price"]])
            st.line_chart(hist[["volume"]])

        st.download_button("📥 Download laatste scan", df.to_csv(index=False).encode("utf-8"), "gta6_v541.csv", "text/csv")

    st.subheader("🔎 Waarom koopt de paperbot niet?")
    latest = query("SELECT scanned_at FROM v541_runs ORDER BY scanned_at DESC LIMIT 1")

    if latest:
        decisions = query("""SELECT name,address,outcome,reason,price,liquidity FROM v541_decisions
            WHERE scan_at=? ORDER BY CASE outcome WHEN 'PAPER_ENTRY' THEN 0 WHEN 'KANDIDAAT' THEN 1
            WHEN 'UITGESCHAKELD' THEN 2 WHEN 'GEBLOKKEERD' THEN 3 ELSE 4 END, name LIMIT 400""", (latest[0][0],))
        decisions_df = pd.DataFrame(decisions, columns=["Token", "Adres", "Besluit", "Reden", "Prijs", "Liquiditeit"])
        st.dataframe(decisions_df, hide_index=True, use_container_width=True)
        st.download_button("📥 Download beslissingslog", decisions_df.to_csv(index=False).encode("utf-8"), "gta6_beslissingen.csv", "text/csv")

    st.subheader("🧪 Paper trading")
    trades = query("""SELECT id,name,address,opened_at,amount_usd,entry_market,last_mark,
        status,pnl_usd,exit_reason FROM paper_v54 ORDER BY id DESC LIMIT 200""")

    if trades:
        tdf = pd.DataFrame(trades, columns=["ID", "Naam", "Adres", "Geopend", "Inzet", "Instap", "Laatste", "Status", "Resultaat", "Exitreden"])
        st.dataframe(tdf, hide_index=True, use_container_width=True)
        closed = tdf[tdf["Status"] == "CLOSED"]
        if not closed.empty:
            st.metric("Gesimuleerd gerealiseerd P/L", f"${closed['Resultaat'].sum():,.2f}")
            st.metric("Winnende trades", f"{(closed['Resultaat'] > 0).mean() * 100:.1f}%")
    else:
        st.info("Nog geen paper-posities. Bekijk het beslissingslog voor de reden.")

    st.subheader("🕒 Scanlog")
    logs = query("SELECT scanned_at,finished_at,fetched,errors,entries,exits,cause FROM v541_runs ORDER BY scanned_at DESC LIMIT 20")
    if logs:
        st.dataframe(pd.DataFrame(logs, columns=["Start UTC", "Einde UTC", "Tokens", "API-fouten", "Instappen", "Uitstappen", "Trigger"]),
                     hide_index=True, use_container_width=True)

    if st.session_state.v541_scan_errors:
        with st.expander("API-fouten"):
            st.write(st.session_state.v541_scan_errors[:30])


dashboard()
st.caption("Onderzoeksprototype. De marktsimulatie verifieert geen echte swap-uitvoerbaarheid; een gesimuleerde stoploss is niet gegarandeerd.")
