                        "Narrative", "Early Discovery", "Momentum", "Risico", "Fase"])


init_db()
st.title("🎮 GTA 6 Narrative Trading Radar V5.1")
st.caption("Solana · GTA-gerelateerde memecoins · Early Discovery + Momentum · Onderzoek / geen echte orders")
st.warning("Een score is geen winstverwachting. Nieuw token ≠ veilig token. Er kunnen scams, wash trading en plotselinge liquiditeitsverliezen voorkomen.")
st.sidebar.header("⚙️ Instellingen")
auto = st.sidebar.toggle("Automatische scan elke 5 minuten (actieve sessie)", value=False)
max_age = st.sidebar.slider("Early Discovery: max. leeftijd in uren", 1, 168, 24)
min_liq = st.sidebar.number_input("Minimum liquiditeit shortlist ($)", min_value=0, value=10000, step=1000)
st.sidebar.caption("Streamlit Community Cloud is niet geschikt voor gegarandeerde 24/7-scans of permanente SQLite-opslag.")

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
    manual = st.button("🔄 Scan nu", type="primary")
    # Streamlit fragment reruns on its schedule; only scan when auto is enabled.
    # Clicking another widget outside this fragment does not initiate a new scan.
    due = auto and (time.monotonic() - st.session_state.v51_last_attempt >= 300)
    if manual or due:
        st.session_state.v51_last_attempt = time.monotonic()
        with st.spinner("GTA-tokens ontdekken en analyseren..."):
            try:
                rows, errors, stamp = run_scan()
                st.session_state.v51_rows = rows
                st.session_state.v51_errors = errors
                st.session_state.v51_time = stamp
            except (requests.RequestException, sqlite3.Error, ValueError) as exc:
                st.error(f"Scanfout: {exc}")
    if st.session_state.v51_rows is not None:
        df = pd.DataFrame(st.session_state.v51_rows)
    else:
        df = last_stored()
    st.caption(f"Laatste scan in deze sessie (UTC): {st.session_state.v51_time or 'Nog niet uitgevoerd'}")
    if df.empty:
        st.info("Nog geen GTA-kandidaten. Klik op 'Scan nu'.")
        return
    a, b, c = st.columns(3)
    a.metric("GTA-kandidaten", len(df))
    b.metric(f"Nieuwe paren (≤{max_age} uur)", int((df["Leeftijd uren"].fillna(1e9) <= max_age).sum()))
    c.metric("Momentum ≥60", int((df["Momentum"] >= 60).sum()))
    st.subheader("🚀 Early Discovery — recent aangemaakte paren")
    young = df[df["Leeftijd uren"].notna() & (df["Leeftijd uren"] <= max_age)].copy()
    st.dataframe(young.sort_values("Leeftijd uren") if not young.empty else young, use_container_width=True, hide_index=True)
    st.subheader("📈 Activiteit en trends")
    active = df[df["Liquiditeit USD"] >= min_liq].sort_values(["Momentum", "Early Discovery"], ascending=False)
    st.dataframe(active, use_container_width=True, hide_index=True)
    with st.expander("Alle GTA-kandidaten"):
        st.dataframe(df, use_container_width=True, hide_index=True)
    options = {f"{r['Naam']} ({r['Ticker']}) — {r['Adres'][:8]}": r["Adres"] for _, r in df.iterrows()}
    selected = st.selectbox("🔍 Token bekijken", list(options), key="v51_selection")
    addr = options[selected]
    token = df[df["Adres"] == addr].iloc[0]
    st.code(addr, language="text")
    st.write("Fase:", token["Fase"], "| Risico:", token["Risico"])
    st.caption("Risicocontroles zijn onvolledig: mint/freeze-authority, LP-lock, top holders en verkoopbaarheid zijn niet geverifieerd.")
    if st.button("➕ Op watchlist", key="v51_watch"):
        with db_connect() as con:
            con.execute("INSERT OR IGNORE INTO watchlist(address,name,added_at) VALUES(?,?,?)", (addr, token["Naam"], now()))
        st.success("Toegevoegd")
    st.link_button("Open op DexScreener", f"https://dexscreener.com/solana/{addr}")
    past = db_all("""SELECT scanned_at, price, volume_m5, liquidity FROM radar_v51
                     WHERE address=? ORDER BY scanned_at DESC LIMIT 100""", (addr,))
    if len(past) >= 2:
        hist = pd.DataFrame(past, columns=["Tijd", "Prijs", "Volume 5m", "Liquiditeit"])
        hist["Tijd"] = pd.to_datetime(hist["Tijd"], utc=True)
        hist = hist.sort_values("Tijd").set_index("Tijd")
        st.line_chart(hist[["Prijs"]])
        st.line_chart(hist[["Volume 5m"]])
    st.download_button("📥 Download scan-CSV", df.to_csv(index=False).encode("utf-8"), "gta6_radar_v51.csv", "text/csv")
    if st.session_state.v51_errors:
        with st.expander(f"⚠️ API-meldingen ({len(st.session_state.v51_errors)})"):
            st.write(st.session_state.v51_errors[:30])


radar_dashboard()
st.divider()
st.subheader("📣 Historische signalen")
signals = db_all("SELECT scanned_at, address, signal FROM token_signals ORDER BY scanned_at DESC LIMIT 50")
if signals:
    st.dataframe(pd.DataFrame(signals, columns=["UTC", "Mint", "Signaal"]), hide_index=True, use_container_width=True)
st.subheader("⭐ Watchlist")
watch = db_all("SELECT name, address, added_at FROM watchlist ORDER BY added_at DESC LIMIT 50")
if watch:
    st.dataframe(pd.DataFrame(watch, columns=["Naam", "Mint", "Toegevoegd"]), hide_index=True, use_container_width=True)
st.caption("V5.1 registreert signalen, maar verstuurt geen orders. Nieuws/sociale trending-feed en paper trading volgen in latere versies.")
