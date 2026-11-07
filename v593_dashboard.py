
"""GTA 6 Radar V5.9.3 — read-only achtergrondpapertradingdashboard."""

import base64
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

REPO = "obede0025-ui/gta6-token-radarr"
BRANCH = "main"
API_ROOT = f"https://api.github.com/repos/{REPO}/contents"
BELGIUM = ZoneInfo("Europe/Brussels")


def _v593_token():
    """Optioneel read-only GitHub-token uit Streamlit Secrets."""
    try:
        return str(st.secrets.get("GITHUB_READ_TOKEN", "")).strip()
    except (FileNotFoundError, KeyError, AttributeError):
        return ""


@st.cache_data(ttl=90, show_spinner=False)
def _v593_read(path, token):
    """Lees JSON van GitHub zonder de repository te wijzigen.

    Deze functie blijft beschikbaar voor v594_dashboard.py.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GTA6Radar-V593",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        f"{API_ROOT}/{path}",
        params={"ref": BRANCH},
        headers=headers,
        timeout=20,
    )

    if response.status_code == 404:
        raise FileNotFoundError(f"GitHub-bestand ontbreekt: {path}")

    if response.status_code == 403:
        raise RuntimeError(
            "GitHub weigert de aanvraag (rechten of API-limiet)."
        )

    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise ValueError(f"Onbekend GitHub-bestandsformaat: {path}")

    encoded = payload.get("content")
    if not isinstance(encoded, str):
        raise ValueError(f"Geen bestandsinhoud gevonden: {path}")

    decoded = base64.b64decode(encoded).decode("utf-8")
    result = json.loads(decoded)
    if not isinstance(result, dict):
        raise ValueError(f"JSON is geen object: {path}")
    return result


def _v593_datetime(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _v593_time(value):
    """Alle tijdstippen in Belgische tijd, automatisch CET/CEST."""
    try:
        dt = _v593_datetime(value)
        if dt is None:
            return "Onbekend"
        return dt.astimezone(BELGIUM).strftime("%d-%m-%Y %H:%M:%S")
    except (ValueError, TypeError, OverflowError):
        return "Ongeldig tijdstip"


def _v593_age(value):
    try:
        dt = _v593_datetime(value)
        if dt is None:
            return None
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except (ValueError, TypeError, OverflowError):
        return None


def _v593_open_rows(state):
    rows = []
    for strategy in ("A", "B"):
        positions = (state.get("open") or {}).get(strategy) or {}
        for address, trade in positions.items():
            rows.append({
                "Strategie": strategy,
                "Token": trade.get("name", "Onbekend"),
                "Mintadres": address,
                "Pair": trade.get("pair"),
                "Instaptijd België": _v593_time(trade.get("opened_at")),
                "Instapprijs $": trade.get("entry_market"),
                "Hoeveelheid": trade.get("quantity"),
            })
    return rows


def _v593_closed_rows(state):
    rows = []
    for strategy in ("A", "B"):
        for trade in (state.get("closed") or {}).get(strategy) or []:
            rows.append({
                "Strategie": strategy,
                "Token": trade.get("name", "Onbekend"),
                "Mintadres": trade.get("token"),
                "Geopend België": _v593_time(trade.get("opened_at")),
                "Gesloten België": _v593_time(trade.get("closed_at")),
                "Reden": trade.get("reason"),
                "P/L $": trade.get("pnl_usd"),
            })
    return rows


@st.fragment(run_every="120s")
def v593_background_dashboard():
    st.divider()
    st.subheader("🛰️ V5.9.3 — Achtergrondpapertrading")
    st.caption(
        "Afzonderlijke GitHub Actions-paperportefeuille. Alle tijden in "
        "Belgische tijd (Europe/Brussels). Alleen-lezen."
    )

    if st.button("🔄 Achtergrondresultaten vernieuwen", key="v593_refresh"):
        _v593_read.clear()

    try:
        token = _v593_token()
        state = _v593_read("data/v591/portfolio.json", token)
        report = _v593_read("data/v591/preview.json", token)
    except (
        requests.RequestException, FileNotFoundError, RuntimeError,
        ValueError, KeyError, UnicodeError, json.JSONDecodeError
    ) as exc:
        st.error(f"Kon GitHub-rapporten niet lezen: {exc}")
        st.info(
            "Controleer of de Learning Collector succesvol draaide en "
            "beide JSON-bestanden op GitHub staan. Voor een private repo "
            "is een read-only GITHUB_READ_TOKEN in Streamlit Secrets nodig."
        )
        return

    if state.get("version") != 1:
        st.error("Onbekende portefeuilleversie. Geen gegevens gewijzigd.")
        return
    if report.get("mode") != "ISOLATED_PAPER_STATE":
        st.error("Dit is geen opgeslagen achtergrondpapertrade-rapport.")
        return
    if not isinstance(state.get("open"), dict) or not isinstance(
        state.get("closed"), dict
    ):
        st.error("Portefeuille heeft een onjuist gegevensformaat.")
        return

    cursor = state.get("cursor")
    report_cursor = report.get("last_processed")
    generated = report.get("generated_utc")
    last_observation = cursor[0] if isinstance(cursor, list) and cursor else None

    if cursor != report_cursor:
        st.warning(
            "De portefeuille en het laatste rapport verwijzen niet "
            "naar dezelfde marktmeting. Controleer GitHub Actions."
        )

    st.write("**Laatste marktmeting (België):**", _v593_time(last_observation))
    st.write("**Laatste rapport (België):**", _v593_time(generated))

    age = _v593_age(generated)
    if age is None:
        st.warning("De rapporttijd ontbreekt of is ongeldig.")
    elif age < -5:
        st.warning("Rapporttijd ligt in de toekomst. Controleer de klok.")
    elif age > 20:
        st.error(
            f"Geen nieuw rapport sinds ongeveer {age:.0f} minuten. "
            "Controleer de GitHub Actions-workflow."
        )
    else:
        st.success(f"Recent achtergrondrapport beschikbaar ({max(age, 0):.0f} min oud).")

    run = report.get("run") or {}
    a, b, c = st.columns(3)
    a.metric("Metingen laatste run", run.get("observations", 0))
    b.metric("Signalen laatste run", run.get("signals", 0))
    c.metric("Modus", "PAPER")

    open_rows = _v593_open_rows(state)
    closed_rows = _v593_closed_rows(state)
    realized = sum(float(t.get("P/L $") or 0) for t in closed_rows)

    st.subheader("📊 Achtergrondportefeuille")
    x, y, z, w = st.columns(4)
    x.metric("Open A", len((state["open"].get("A") or {})))
    y.metric("Open B", len((state["open"].get("B") or {})))
    z.metric("Afgesloten", len(closed_rows))
    w.metric("Gerealiseerde P/L", f"${realized:+.2f}")

    tab1, tab2, tab3 = st.tabs([
        "📌 Open posities", "✅ Afgesloten trades", "📋 Laatste scan"
    ])

    with tab1:
        if open_rows:
            frame = pd.DataFrame(open_rows)
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button(
                "📥 Open trades CSV",
                frame.to_csv(index=False).encode("utf-8"),
                "v593_open.csv",
                "text/csv",
                key="v593_open_csv",
            )
        else:
            st.info("Nog geen open achtergrondposities.")

    with tab2:
        if closed_rows:
            frame = pd.DataFrame(closed_rows)
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button(
                "📥 Gesloten trades CSV",
                frame.to_csv(index=False).encode("utf-8"),
                "v593_closed.csv",
                "text/csv",
                key="v593_closed_csv",
            )
        else:
            st.info("Nog geen afgesloten achtergrondtrades.")

    with tab3:
        st.json({
            "laatste_meting_belgie": _v593_time(last_observation),
            "laatste_rapport_belgie": _v593_time(generated),
            "run": run,
            "portfolio": report.get("portfolio", {}),
        })

    st.caption(
        "GitHub Actions heeft geen gegarandeerd scaninterval. "
        "Virtuele fills, stop-losses en resultaten zijn simulaties. "
        "Dit dashboard schrijft niet naar SQLite, GitHub of Supabase."
    )
