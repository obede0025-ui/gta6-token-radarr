"""GTA 6 Radar V6.3 — centraal read-only overzicht GitHub-paperstrategieën.

Toont A/B/C uit de bestaande GitHub Actions-portefeuille, zonder deze samen
te voegen met lokale Streamlit/Supabase-papertrades. Geen database-wijzigingen.
Behoudt publieke helpers voor v594_dashboard.py.
"""

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
STRATEGIES = ("A", "B", "C")


def _v593_token():
    """Optioneel GitHub-leestoken via Streamlit Secrets."""
    try:
        return str(st.secrets.get("GITHUB_READ_TOKEN", "")).strip()
    except (FileNotFoundError, KeyError, AttributeError):
        return ""


@st.cache_data(ttl=90, show_spinner=False)
def _v593_read(path, token):
    """GitHub-JSON uitsluitend lezen. Ook gebruikt door v594_dashboard.py."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GTA6Radar-V63",
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
        raise RuntimeError("GitHub weigert de aanvraag (rechten of API-limiet).")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise ValueError(f"Onbekend GitHub-bestandsformaat: {path}")
    encoded = payload.get("content")
    if not isinstance(encoded, str):
        raise ValueError(f"Geen bestandsinhoud gevonden: {path}")
    result = json.loads(base64.b64decode(encoded).decode("utf-8"))
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
    try:
        dt = _v593_datetime(value)
        return dt.astimezone(BELGIUM).strftime("%d-%m-%Y %H:%M:%S") if dt else "Onbekend"
    except (ValueError, TypeError, OverflowError):
        return "Ongeldig tijdstip"


def _v593_age(value):
    try:
        dt = _v593_datetime(value)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60 if dt else None
    except (ValueError, TypeError, OverflowError):
        return None


def _v593_amount(value):
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def _v593_validate(state, report):
    if state.get("version") != 1:
        raise ValueError("Onbekende portefeuilleversie; niets gewijzigd.")
    if report.get("mode") != "ISOLATED_PAPER_STATE":
        raise ValueError("Dit is geen opgeslagen achtergrondpapertrade-rapport.")
    for field in ("open", "closed", "traded"):
        if not isinstance(state.get(field), dict):
            raise ValueError(f"Ongeldige portefeuille-inhoud: {field}")
    for strategy in STRATEGIES:
        if not isinstance(state["open"].get(strategy, {}), dict):
            raise ValueError(f"Ongeldige open-posities: {strategy}")
        if not isinstance(state["closed"].get(strategy, []), list):
            raise ValueError(f"Ongeldige afgesloten trades: {strategy}")


def _v593_open_rows(state):
    rows = []
    for strategy in STRATEGIES:
        for address, trade in (state["open"].get(strategy) or {}).items():
            rows.append({
                "Bron": "GitHub achtergrond",
                "Strategie": strategy,
                "Token": trade.get("name", "Onbekend"),
                "Mintadres": address,
                "Pair": trade.get("pair"),
                "Instaptijd België": _v593_time(trade.get("opened_at")),
                "Instapprijs $": _v593_amount(trade.get("entry_market")),
                "Hoeveelheid": _v593_amount(trade.get("quantity")),
                "Status": "OPEN (PAPER)",
            })
    return rows


def _v593_closed_rows(state):
    rows = []
    for strategy in STRATEGIES:
        for trade in (state["closed"].get(strategy) or []):
            rows.append({
                "Bron": "GitHub achtergrond",
                "Strategie": strategy,
                "Token": trade.get("name", "Onbekend"),
                "Mintadres": trade.get("token"),
                "Geopend België": _v593_time(trade.get("opened_at")),
                "Gesloten België": _v593_time(trade.get("closed_at")),
                "Exitreden": trade.get("reason"),
                "Gerealiseerde P/L $": _v593_amount(trade.get("pnl_usd")),
                "Status": "CLOSED (PAPER)",
            })
    return rows


def _v593_strategy_rows(state):
    rows = []
    for strategy in STRATEGIES:
        trades = state["closed"].get(strategy) or []
        pnl = sum(_v593_amount(t.get("pnl_usd")) or 0 for t in trades)
        valid_pnl = [_v593_amount(t.get("pnl_usd")) for t in trades]
        valid_pnl = [v for v in valid_pnl if v is not None]
        rows.append({
            "Bron": "GitHub achtergrond",
            "Strategie": strategy,
            "Open": len(state["open"].get(strategy) or {}),
            "Gesloten": len(trades),
            "Gerealiseerde P/L $": round(pnl, 4),
            "Winrate gesloten": (
                f"{100 * sum(v > 0 for v in valid_pnl) / len(valid_pnl):.1f}%"
                if valid_pnl else "—"
            ),
        })
    return rows


@st.fragment(run_every="120s")
def v593_background_dashboard():
    st.divider()
    st.subheader("🛰️ V6.3 — Achtergrondpapertrading A/B/C")
    st.caption(
        "Bron: GitHub Actions → data/v591/portfolio.json. "
        "Dit zijn uitsluitend virtuele trades; de bestaande Streamlit- "
        "en Supabase-paperportfolio's blijven volledig gescheiden. "
        "Alle tijden zijn Belgische tijd."
    )

    if st.button("🔄 Achtergrondresultaten vernieuwen", key="v593_refresh"):
        _v593_read.clear()

    try:
        token = _v593_token()
        state = _v593_read("data/v591/portfolio.json", token)
        report = _v593_read("data/v591/preview.json", token)
        _v593_validate(state, report)
    except (
        requests.RequestException, FileNotFoundError, RuntimeError,
        ValueError, KeyError, UnicodeError, json.JSONDecodeError
    ) as exc:
        st.error(f"Kon GitHub-paperportefeuille niet laden: {exc}")
        st.info(
            "Controleer de GitHub Actions-run en of beide JSON-bestanden bestaan. "
            "Een private repository vereist een read-only GITHUB_READ_TOKEN."
        )
        return

    cursor = state.get("cursor")
    report_cursor = report.get("last_processed")
    generated = report.get("generated_utc")
    last_observation = cursor[0] if isinstance(cursor, list) and cursor else None
    if cursor != report_cursor:
        st.warning(
            "Portefeuille en rapport verwijzen niet naar dezelfde marktmeting. "
            "De gegevens kunnen tijdelijk niet synchroon zijn."
        )
    st.write("**Laatste marktmeting (België):**", _v593_time(last_observation))
    st.write("**Laatste rapport (België):**", _v593_time(generated))
    age = _v593_age(generated)
    if age is None:
        st.warning("Het rapporttijdstip ontbreekt of is ongeldig.")
    elif age < -5:
        st.warning("Rapporttijd ligt in de toekomst. Controleer de klok.")
    elif age > 20:
        st.error(
            f"Geen nieuw achtergrondrapport sinds ongeveer {age:.0f} minuten. "
            "Controleer GitHub Actions."
        )
    else:
        st.success(f"Achtergrondrapport beschikbaar ({max(age, 0):.0f} min oud).")

    run = report.get("run") or {}
    a, b, c = st.columns(3)
    a.metric("Metingen laatste run", run.get("observations", 0))
    b.metric("Signalen laatste run", run.get("signals", 0))
    c.metric("Modus", "PAPER")

    open_rows = _v593_open_rows(state)
    closed_rows = _v593_closed_rows(state)
    strategies = _v593_strategy_rows(state)
    realized = sum(row["Gerealiseerde P/L $"] for row in strategies)

    st.subheader("📊 GitHub-achtergrondportefeuille — A / B / C")
    a, b, c, d = st.columns(4)
    a.metric("Open A", strategies[0]["Open"])
    b.metric("Open B", strategies[1]["Open"])
    c.metric("Open C", strategies[2]["Open"])
    d.metric("Gerealiseerde P/L", f"${realized:+.2f}")
    st.dataframe(pd.DataFrame(strategies), hide_index=True, use_container_width=True)

    signal_counts = run.get("signals_by_strategy")
    if isinstance(signal_counts, dict):
        st.caption(
            "Signalen laatste run — "
            + " · ".join(f"{s}: {int(signal_counts.get(s) or 0)}" for s in STRATEGIES)
        )

    tab1, tab2, tab3 = st.tabs([
        "📌 Open posities A/B/C", "✅ Afgesloten trades A/B/C", "📋 Laatste scan"
    ])
    with tab1:
        if open_rows:
            frame = pd.DataFrame(open_rows)
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button(
                "📥 Open achtergrondtrades CSV",
                frame.to_csv(index=False).encode("utf-8"),
                "github_paper_open_abc.csv", "text/csv", key="v593_open_csv",
            )
        else:
            st.info("Geen open posities in de GitHub-achtergrondportefeuille.")
        st.caption(
            "Een open positie is niet automatisch een realiseerbare winst. "
            "Er wordt geen ongerealiseerde P/L getoond zonder een "
            "controleerbare recente prijs en liquiditeit."
        )

    with tab2:
        if closed_rows:
            frame = pd.DataFrame(closed_rows)
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button(
                "📥 Gesloten achtergrondtrades CSV",
                frame.to_csv(index=False).encode("utf-8"),
                "github_paper_closed_abc.csv", "text/csv", key="v593_closed_csv",
            )
        else:
            st.info("Geen afgesloten trades in de GitHub-achtergrondportefeuille.")

    with tab3:
        st.json({
            "bron": "GitHub Actions — geïsoleerde paperportfolio",
            "laatste_meting_belgie": _v593_time(last_observation),
            "laatste_rapport_belgie": _v593_time(generated),
            "run": run,
            "portfolio": report.get("portfolio", {}),
        })

    st.caption(
        "Dit overzicht leest alleen GitHub-JSON en schrijft niet naar "
        "SQLite, GitHub of Supabase. De lokale Streamlit-trades worden "
        "hier niet dubbelgeteld. GitHub Actions heeft geen gegarandeerd "
        "scaninterval; alle transacties en exits zijn simulaties."
    )
