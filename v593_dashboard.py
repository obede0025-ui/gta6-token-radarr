
"""GTA6 V5.9.3 - read-only background dashboard."""

import base64
import json
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st


REPO = "obede0025-ui/gta6-token-radarr"
BRANCH = "main"

API_ROOT = (
    f"https://api.github.com/repos/{REPO}/contents"
)


def _v593_token():
    try:
        token = st.secrets.get("GITHUB_READ_TOKEN", "")
    except (FileNotFoundError, KeyError, AttributeError):
        token = ""

    return str(token).strip()


@st.cache_data(ttl=90, show_spinner=False)
def _v593_read(path, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GTA6Radar-V593"
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        f"{API_ROOT}/{path}",
        params={"ref": BRANCH},
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    wrapper = response.json()

    if (
        not isinstance(wrapper, dict)
        or wrapper.get("encoding") != "base64"
    ):
        raise ValueError(
            f"GitHub gaf geen geldig bestand terug: {path}"
        )

    decoded = base64.b64decode(
        wrapper["content"]
    ).decode("utf-8")

    result = json.loads(decoded)

    if not isinstance(result, dict):
        raise ValueError(
            f"Ongeldig JSON-object: {path}"
        )

    return result


def _v593_time(value):
    if not value:
        return "Onbekend"

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(
            timezone.utc
        ).strftime("%d-%m-%Y %H:%M UTC")

    except (TypeError, ValueError):
        return str(value)


def _v593_open_rows(state):
    rows = []

    for strategy in ("A", "B"):
        positions = (
            state.get("open", {})
            .get(strategy, {})
        )

        for address, item in positions.items():
            rows.append({
                "Strategie": strategy,
                "Token": item.get("name"),
                "Mintadres": address,
                "Pair": item.get("pair"),
                "Instaptijd": item.get("opened_at"),
                "Instapprijs $": item.get("entry_market"),
                "Hoeveelheid": item.get("quantity")
            })

    return rows


def _v593_closed_rows(state):
    rows = []

    for strategy in ("A", "B"):
        trades = (
            state.get("closed", {})
            .get(strategy, [])
        )

        for item in trades:
            rows.append({
                "Strategie": strategy,
                "Token": item.get("name"),
                "Mintadres": item.get("token"),
                "Geopend": item.get("opened_at"),
                "Gesloten": item.get("closed_at"),
                "Reden": item.get("reason"),
                "P/L $": item.get("pnl_usd")
            })

    return rows


@st.fragment(run_every="120s")
def v593_background_dashboard():
    st.divider()

    st.subheader(
        "🛰️ V5.9.3 - Achtergrondpapertrading"
    )

    st.caption(
        "Resultaten van GitHub Actions. "
        "Deze portefeuille staat volledig los van "
        "de huidige Streamlit-strategieën A/B."
    )

    if st.button(
        "🔄 Achtergrondresultaten vernieuwen",
        key="v593_refresh"
    ):
        _v593_read.clear()

    try:
        token = _v593_token()

        state = _v593_read(
            "data/v591/portfolio.json",
            token
        )

        report = _v593_read(
            "data/v591/preview.json",
            token
        )

    except (
        requests.RequestException,
        ValueError,
        KeyError,
        UnicodeError
    ) as exc:
        st.error(
            "GitHub-gegevens niet bereikbaar: "
            + str(exc)
        )

        st.info(
            "Controleer of beide bestanden bestaan. "
            "Voor een private GitHub-repository is "
            "een read-only GitHub-token nodig."
        )
        return

    if (
        state.get("version") != 1
        or report.get("mode") != "ISOLATED_PAPER_STATE"
    ):
        st.error(
            "Onbekende portfolio- of rapportversie."
        )
        return

    cursor = state.get("cursor")
    report_cursor = report.get("last_processed")

    if cursor != report_cursor:
        st.warning(
            "Portfolio en rapport komen mogelijk "
            "niet uit dezelfde uitvoering."
        )

    st.write(
        "**Laatste verwerking:**",
        _v593_time(
            cursor[0]
            if isinstance(cursor, list) and cursor
            else None
        )
    )

    generated = report.get("generated_utc")

    st.write(
        "**Laatste rapport:**",
        _v593_time(generated)
    )

    try:
        dt = datetime.fromisoformat(
            generated.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        minutes = (
            datetime.now(timezone.utc)
            - dt.astimezone(timezone.utc)
        ).total_seconds() / 60

        if minutes > 20:
            st.warning(
                "Laatste rapport ouder dan 20 minuten. "
                "Controleer GitHub Actions."
            )

    except (ValueError, TypeError, AttributeError):
        pass

    run = report.get("run") or {}

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Metingen laatste run",
        run.get("observations", 0)
    )

    c2.metric(
        "Signalen laatste run",
        run.get("signals", 0)
    )

    c3.metric(
        "Modus",
        "PAPER"
    )

    open_rows = _v593_open_rows(state)
    closed_rows = _v593_closed_rows(state)

    pnl = sum(
        float(item.get("P/L $") or 0)
        for item in closed_rows
    )

    st.subheader("📊 Achtergrondportefeuille")

    a, b, c, d = st.columns(4)

    a.metric(
        "Open A",
        len(state["open"]["A"])
    )

    b.metric(
        "Open B",
        len(state["open"]["B"])
    )

    c.metric(
        "Afgesloten",
        len(closed_rows)
    )

    d.metric(
        "Gerealiseerde P/L",
        f"${pnl:+.2f}"
    )

    tab1, tab2, tab3 = st.tabs([
        "📌 Open posities",
        "✅ Afgesloten trades",
        "📋 Laatste scan"
    ])

    with tab1:
        if open_rows:
            df = pd.DataFrame(open_rows)

            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True
            )

            st.download_button(
                "📥 Open trades CSV",
                df.to_csv(index=False).encode("utf-8"),
                "v593_open.csv",
                "text/csv",
                key="v593_open_csv"
            )
        else:
            st.info(
                "Nog geen open achtergrondposities."
            )

    with tab2:
        if closed_rows:
            df = pd.DataFrame(closed_rows)

            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True
            )

            st.download_button(
                "📥 Gesloten trades CSV",
                df.to_csv(index=False).encode("utf-8"),
                "v593_closed.csv",
                "text/csv",
                key="v593_closed_csv"
            )
        else:
            st.info(
                "Nog geen afgesloten achtergrondtrades."
            )

    with tab3:
        st.json({
            "last_processed": report_cursor,
            "run": run,
            "portfolio": report.get("portfolio", {})
        })

    st.caption(
        "Alleen-lezen dashboard. "
        "Geen echte orders of gegarandeerde fills. "
        "De papertrade-database en Supabase "
        "worden niet gewijzigd."
    )
