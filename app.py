
import os
import re
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="GTA 6 Token Radar V5", page_icon="🎮", layout="wide")

API = "https://api.dexscreener.com"
RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
DB = os.getenv("GTA6_RADAR_DB", "gta6_radar.db")
SEARCH = ["GTA6", "GTA 6", "GTA VI", "Grand Theft Auto", "Vice City", "Leonida", "Port Gellhorn"]
STRONG = ["gta6", "gta 6", "gta vi", "grand theft auto", "grandtheftauto"]
RELATED = ["vice city", "leonida", "port gellhorn", "lucia", "jason duval", "ambrosia"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def connection():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA busy_timeout = 20000")
    return con


def execute(sql, values=()):
    with connection() as con:
        con.execute(sql, values)


def fetch(sql, values=()):
    with connection() as con:
        return con.execute(sql, values).fetchall()


def init_db():
    # IF NOT EXISTS: oude gegevens en tabellen worden niet verwijderd.
    schemas = [
        "CREATE TABLE IF NOT EXISTS watchlist (address TEXT PRIMARY KEY, name TEXT, added_at TEXT)",
        "CREATE TABLE IF NOT EXISTS snapshots (address TEXT, scanned_at TEXT, price REAL, liquidity REAL, volume24 REAL, buys24 INTEGER, sells24 INTEGER)",
        "CREATE TABLE IF NOT EXISTS discovered (address TEXT PRIMARY KEY, name TEXT, symbol TEXT, first_seen TEXT, last_seen TEXT)",
        "CREATE TABLE IF NOT EXISTS wallet_snapshots (address TEXT, scanned_at TEXT, top10_percent REAL, top20_percent REAL, sampled_accounts INTEGER)",
        "CREATE TABLE IF NOT EXISTS discovery_events (address TEXT PRIMARY KEY, detected_at TEXT, name TEXT, symbol TEXT, pair_age_hours REAL, source TEXT)",
        "CREATE TABLE IF NOT EXISTS momentum_snapshots (address TEXT, scanned_at TEXT, volume_m5 REAL, buys_m5 INTEGER, sells_m5 INTEGER, liquidity REAL, price REAL, PRIMARY KEY(address, scanned_at))",
        "CREATE TABLE IF NOT EXISTS token_signals (address TEXT, scanned_at TEXT, signal TEXT, detail TEXT, PRIMARY KEY(address, scanned_at, signal))",
        "CREATE TABLE IF NOT EXISTS scan_runs (scanned_at TEXT PRIMARY KEY, candidates INTEGER, errors INTEGER)",
    ]
    with connection() as con:
        for sql in schemas:
            con.execute(sql)


def get_json(url, params=None):
    response = requests.get(url, params=params, timeout=15, headers={"User-Agent": "GTA6-Radar-V5"})
    response.raise_for_status()
    return response.json()


def match(text, terms):
    text = str(text or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) for term in terms)


def narrative(name, ticker, description, links):
    title = f"{name} {ticker}"
    all_text = f"{title} {description} {links}"
    if match(title, STRONG):
        return 100, "Directe GTA 6-match"
    if match(all_text, STRONG):
        return 85, "GTA 6-context in profiel"
    if match(title, RELATED):
        return 55, "Gerelateerde naam"
    if match(all_text, RELATED):
        return 35, "Gerelateerde context"
    return 0, "Geen match"


def discover():
    candidates, errors = {}, []
    for endpoint in ["/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"]:
        try:
            data = get_json(API + endpoint)
            for item in data if isinstance(data, list) else []:
                if item.get("chainId") == "solana" and item.get("tokenAddress"):
                    candidates[item["tokenAddress"]] = item
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"Discovery {endpoint}: {exc}")
    for term in SEARCH:
        try:
            data = get_json(API + "/latest/dex/search", {"q": term})
            for pair in data.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                # Zoekresultaten bevatten beide kanten; de narratiefilter beslist later.
                for side in ("baseToken", "quoteToken"):
                    address = (pair.get(side) or {}).get("address")
                    if address:
                        candidates.setdefault(address, {"tokenAddress": address, "description": "", "url": ""})
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"Zoekterm {term}: {exc}")
    for address, name in fetch("SELECT address, name FROM watchlist"):
        candidates.setdefault(address, {"tokenAddress": address, "description": "", "url": ""})
    return candidates, errors


def main_pair(address):
    pairs = get_json(API + f"/token-pairs/v1/solana/{address}")
    if not isinstance(pairs, list):
        return None
    relevant = [p for p in pairs if p.get("chainId") == "solana" and
                address.lower() in [(p.get("baseToken") or {}).get("address", "").lower(),
                                    (p.get("quoteToken") or {}).get("address", "").lower()]]
    # Geef de voorkeur aan een pair waar het onderzochte token de baseToken is.
    relevant.sort(key=lambda p: (int((p.get("baseToken") or {}).get("address", "").lower() == address.lower()),
                                 number((p.get("liquidity") or {}).get("usd"))), reverse=True)
    return relevant[0] if relevant else None


def analyze(address, profile, pair):
    base, quote = pair.get("baseToken") or {}, pair.get("quoteToken") or {}
    token = base if base.get("address", "").lower() == address.lower() else quote
    name, symbol = token.get("name") or "Unknown", token.get("symbol") or "?"
    info = pair.get("info") or {}
    links = " ".join(str(x.get("url", "")) for x in (info.get("socials") or []) + (info.get("websites") or []))
    desc = str(profile.get("description") or "")
    ns, category = narrative(name, symbol, desc, links)
    if ns == 0:
        return None
    created = number(pair.get("pairCreatedAt"))
    age = max(0, (datetime.now(timezone.utc).timestamp() * 1000 - created) / 3600000) if created else None
    tx5 = (pair.get("txns") or {}).get("m5") or {}
    tx24 = (pair.get("txns") or {}).get("h24") or {}
    volume = pair.get("volume") or {}
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    buys5, sells5 = int(number(tx5.get("buys"))), int(number(tx5.get("sells")))
    risks = []
    if liquidity < 10000:
        risks.append("Zeer lage liquiditeit")
    if liquidity == 0:
        risks.append("Liquiditeit ontbreekt of is nul")
    if age is None:
        risks.append("Leeftijd onbekend")
    elif age < 24:
        risks.append("Nieuw paar: extra risico")
    if buys5 + sells5 < 5:
        risks.append("Weinig 5m-transacties")
    if not links:
        risks.append("Geen websites/socials gevonden")
    score = min(100, round(ns * 0.45 + min(liquidity / 10000, 15) +
                           min(number(volume.get("m5")) / 500, 20) + min(buys5, 20)))
    return {
        "Naam": name, "Ticker": symbol, "Adres": address, "Narratief": category,
        "Narrative score": ns, "Onderzoeksscore": score, "Prijs USD": number(pair.get("priceUsd")),
        "Liquiditeit USD": liquidity, "Volume 5m USD": number(volume.get("m5")),
        "Volume 24u USD": number(volume.get("h24")), "Koop 5m": buys5, "Verkoop 5m": sells5,
        "Koop 24u": int(number(tx24.get("buys"))), "Verkoop 24u": int(number(tx24.get("sells"))),
        "Leeftijd uren": round(age, 2) if age is not None else None,
        "Risico's": ", ".join(risks) if risks else "Geen basiswaarschuwing",
        "DEX": pair.get("url") or profile.get("url") or "",
    }


def store(row, scanned_at):
    a = row["Adres"]
    old = fetch("SELECT volume_m5, buys_m5, sells_m5 FROM momentum_snapshots WHERE address=? ORDER BY scanned_at DESC LIMIT 1", (a,))
    signals = []
    if not fetch("SELECT 1 FROM discovery_events WHERE address=?", (a,)):
        execute("INSERT OR IGNORE INTO discovery_events VALUES (?, ?, ?, ?, ?, ?)",
                (a, scanned_at, row["Naam"], row["Ticker"], row["Leeftijd uren"], "dexscreener"))
        signals.append("EERSTE ONTDEKKING")
    if old:
        prev_volume, prev_buys, prev_sells = old[0]
        if prev_volume and row["Volume 5m USD"] >= 2 * prev_volume and row["Volume 5m USD"] >= 500:
            signals.append("VOLUMEPIEK 5M")
        if row["Koop 5m"] >= 10 and row["Koop 5m"] > 2 * max(row["Verkoop 5m"], 1):
            signals.append("KOOPDRUK 5M")
    execute("INSERT INTO snapshots (address, scanned_at, price, liquidity, volume24, buys24, sells24) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a, scanned_at, row["Prijs USD"], row["Liquiditeit USD"], row["Volume 24u USD"], row["Koop 24u"], row["Verkoop 24u"]))
    execute("INSERT INTO discovered (address, name, symbol, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET name=excluded.name, symbol=excluded.symbol, last_seen=excluded.last_seen",
            (a, row["Naam"], row["Ticker"], scanned_at, scanned_at))
    execute("INSERT OR REPLACE INTO momentum_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a, scanned_at, row["Volume 5m USD"], row["Koop 5m"], row["Verkoop 5m"], row["Liquiditeit USD"], row["Prijs USD"]))
    for signal in signals:
        execute("INSERT OR IGNORE INTO token_signals VALUES (?, ?, ?, ?)", (a, scanned_at, signal, "Heuristisch onderzoeksignaal"))
    row["Signalen"] = ", ".join(signals)


def scan():
    candidates, errors = discover()
    scanned_at = utc_now()
    results = []
    for address, profile in candidates.items():
        try:
            pair = main_pair(address)
            if pair:
                item = analyze(address, profile, pair)
                if item:
                    store(item, scanned_at)
                    results.append(item)
        except (requests.RequestException, ValueError, KeyError, sqlite3.Error) as exc:
            errors.append(f"{address[:12]}: {exc}")
    execute("INSERT OR REPLACE INTO scan_runs VALUES (?, ?, ?)", (scanned_at, len(results), len(errors)))
    return pd.DataFrame(results), errors, scanned_at


def holder_sample(mint):
    def rpc(method, params):
        response = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data["result"]
    largest = rpc("getTokenLargestAccounts", [mint])["value"]
    supply = number(rpc("getTokenSupply", [mint])["value"].get("uiAmountString"))
    addresses = [x["address"] for x in largest if x.get("address")]
    accounts = rpc("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])["value"] if addresses else []
    owners = {}
    for account in accounts:
        try:
            info = account["data"]["parsed"]["info"]
            owner = info["owner"]
            owners[owner] = owners.get(owner, 0) + number(info["tokenAmount"].get("uiAmountString"))
        except (KeyError, TypeError):
            continue
    ordered = sorted(owners.items(), key=lambda x: x[1], reverse=True)
    top10 = 100 * sum(v for _, v in ordered[:10]) / supply if supply else 0
    top20 = 100 * sum(v for _, v in ordered[:20]) / supply if supply else 0
    execute("INSERT INTO wallet_snapshots VALUES (?, ?, ?, ?, ?)", (mint, utc_now(), top10, top20, len(addresses)))
    return top10, top20, ordered


init_db()
st.title("🎮 GTA 6 Token Radar V5.0")
st.caption("Solana • Early Discovery • Research only • Geen automatische trading")
st.warning("Scores zijn heuristieken, geen koopadviezen. Een GTA-naam bewijst geen officiële Rockstar-connectie. Nieuwe memecoins kunnen volledig waardeloos worden.")

if "results" not in st.session_state:
    st.session_state.results = pd.DataFrame()
if "errors" not in st.session_state:
    st.session_state.errors = []
if "last_scan" not in st.session_state:
    st.session_state.last_scan = "Nog niet gescand"

st.sidebar.header("⚙️ Scannerinstellingen")
auto = st.sidebar.toggle("Automatisch elke 5 minuten", value=False)
max_age = st.sidebar.number_input("Maximale leeftijd Early Discovery (uren)", min_value=1, value=24)
min_liq = st.sidebar.number_input("Minimum liquiditeit shortlist ($)", min_value=0, value=10000, step=1000)
st.sidebar.caption("Automatische scans werken alleen bij een actieve app-sessie; hosting kan de app pauzeren.")


def perform_scan():
    with st.spinner("DexScreener wordt gecontroleerd..."):
        try:
            df, errors, timestamp = scan()
            st.session_state.results = df
            st.session_state.errors = errors
            st.session_state.last_scan = timestamp
        except Exception as exc:
            st.error(f"Scanfout: {exc}")


if st.button("🔄 Start scan", type="primary"):
    perform_scan()

# Een actieve Streamlit-sessie voert elke 300 seconden een fragment opnieuw uit.
# Vermijd gelijktijdige scans door de scan uitsluitend in deze fragmentfunctie te doen.
@st.fragment(run_every="300s")
def auto_scan_fragment():
    if auto:
        perform_scan()
        st.caption(f"Automatische scan voltooid: {st.session_state.last_scan}")

auto_scan_fragment()

df = st.session_state.results
st.caption(f"Laatste scan (UTC): {st.session_state.last_scan}")
if not df.empty:
    c1, c2, c3 = st.columns(3)
    c1.metric("GTA-kandidaten", len(df))
    c2.metric("Jonge paren", int((df["Leeftijd uren"].fillna(1e9) <= max_age).sum()))
    c3.metric("Nieuwe signalen", int(df["Signalen"].fillna("").ne("").sum()))
    st.subheader("🚀 Early Discovery")
    young = df[(df["Leeftijd uren"].notna()) & (df["Leeftijd uren"] <= max_age)]
    st.dataframe(young.sort_values("Leeftijd uren"), use_container_width=True, hide_index=True)
    st.subheader("⭐ Shortlist")
    shortlist = df[df["Liquiditeit USD"] >= min_liq].sort_values("Onderzoeksscore", ascending=False)
    st.dataframe(shortlist, use_container_width=True, hide_index=True)
    with st.expander("Alle kandidaten"):
        st.dataframe(df, use_container_width=True, hide_index=True)
    options = {f"{r['Naam']} ({r['Ticker']}) — {r['Adres'][:9]}": r["Adres"] for _, r in df.iterrows()}
    selected = st.selectbox("🔍 Onderzoek een token", list(options))
    address = options[selected]
    row = df[df["Adres"] == address].iloc[0]
    st.code(address)
    st.write("Risico's:", row["Risico's"])
    if row["DEX"]:
        st.link_button("Bekijk op DexScreener", row["DEX"])
    if st.button("➕ Voeg toe aan watchlist"):
        execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?)", (address, row["Naam"], utc_now()))
        st.success("Toegevoegd")
    if st.button("👛 Analyseer 20 grootste tokenaccounts"):
        try:
            top10, top20, owners = holder_sample(address)
            st.metric("Top 10 binnen steekproef (%)", f"{top10:.2f}")
            st.metric("Top 20 binnen steekproef (%)", f"{top20:.2f}")
            st.dataframe(pd.DataFrame(owners, columns=["Eigenaar", "Balans"]))
            st.info("Dit is slechts een steekproef van maximaal 20 accounts; pools en speciale wallets zijn niet uitgesloten.")
        except Exception as exc:
            st.error(f"Walletanalyse niet beschikbaar: {exc}")
    history = fetch("SELECT scanned_at, price, liquidity, volume24 FROM snapshots WHERE address=? ORDER BY scanned_at", (address,))
    if len(history) >= 2:
        h = pd.DataFrame(history, columns=["Tijd", "Prijs", "Liquiditeit", "Volume24"])
        h["Tijd"] = pd.to_datetime(h["Tijd"], utc=True)
        st.line_chart(h.set_index("Tijd")[["Prijs"]])
        st.line_chart(h.set_index("Tijd")[["Liquiditeit"]])
        st.line_chart(h.set_index("Tijd")[["Volume24"]])
    st.download_button("📥 Download CSV", df.to_csv(index=False).encode("utf-8"), "gta6_v5.csv", "text/csv")
else:
    st.info("Klik op 'Start scan' om de eerste kandidaten op te halen.")

st.subheader("📡 Laatste signalen")
signals = fetch("SELECT scanned_at, address, signal, detail FROM token_signals ORDER BY scanned_at DESC LIMIT 50")
if signals:
    st.dataframe(pd.DataFrame(signals, columns=["Tijd", "Adres", "Signaal", "Uitleg"]), hide_index=True, use_container_width=True)

st.subheader("⭐ Watchlist")
watch = fetch("SELECT name, address, added_at FROM watchlist ORDER BY added_at DESC")
if watch:
    st.dataframe(pd.DataFrame(watch, columns=["Naam", "Adres", "Toegevoegd"]), hide_index=True, use_container_width=True)

st.subheader("🗂️ Discovery events")
events = fetch("SELECT detected_at, name, symbol, address FROM discovery_events ORDER BY detected_at DESC LIMIT 100")
if events:
    st.dataframe(pd.DataFrame(events, columns=["Tijd", "Naam", "Ticker", "Adres"]), hide_index=True, use_container_width=True)
if st.session_state.errors:
    with st.expander("⚠️ Technische meldingen"):
        st.write(st.session_state.errors[:25])
