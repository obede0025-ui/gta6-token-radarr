
"""V5.9.4 - scanbewaking in Belgische tijd."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from v593_dashboard import _v593_read, _v593_token


BE = ZoneInfo("Europe/Brussels")


def datetime_utc(value):
    if not value:
        return None

    result = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def belgian(value):
    try:
        dt = datetime_utc(value)

        if dt is None:
            return "Onbekend"

        return dt.astimezone(BE).strftime(
            "%d-%m-%Y %H:%M:%S"
        )

    except (ValueError, TypeError):
        return "Ongeldig tijdstip"


@st.fragment(run_every="120s")
def v594_health_dashboard():
    st.divider()

    st.subheader(
        "🩺 V5.9.4 — Scanhistoriek en bewaking"
    )

    st.caption(
        "Alle tijdstippen zijn Belgische tijd. "
        "GitHub Actions kan geplande runs "
        "vertragen of overslaan."
    )

    if st.button(
        "🔄 Scanstatus vernieuwen",
        key="v594_refresh"
    ):
        _v593_read.clear()

    try:
        token = _v593_token()

        health = _v593_read(
            "data/v594/scan_history.json",
            token
        )

        latest = _v593_read(
            "data/latest_scan.json",
            token
        )

        paper = _v593_read(
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
            "Scanstatus niet beschikbaar: "
            + str(exc)
        )

        st.info(
            "Controleer of de eerste V5.9.4 "
            "Collector-run geslaagd is."
        )
        return

    if (
        health.get("schema_version") != 1
        or not isinstance(health.get("runs"), list)
    ):
        st.error(
            "Onbekende of beschadigde scanhistoriek."
        )
        return

    runs = health["runs"]

    if not runs:
        st.info(
            "Nog geen V5.9.4-metingen opgeslagen."
        )
        return

    current = runs[-1]

    try:
        age = (
            datetime.now(timezone.utc)
            - datetime_utc(current["finished_at_utc"])
        ).total_seconds() / 60

    except (KeyError, TypeError, ValueError) as exc:
        st.error(
            "Ongeldige tijd in scanhistoriek: "
            + str(exc)
        )
        return

    if age < -5:
        st.warning(
            "De collector-tijd lijkt in de toekomst "
            "te liggen."
        )

    elif age > 20:
        st.error(
            f"Geen nieuwe voltooide run sinds "
            f"ongeveer {age:.0f} minuten. "
            "Controleer GitHub Actions."
        )

    elif current.get("status") != "OK":
        st.warning(
            "Laatste collector-run bevat waarschuwingen."
        )

    else:
        st.success(
            "Recent collector- en paper-engine-"
            "rapport beschikbaar."
        )

    if (
        latest.get("finished_at_utc")
        != current.get("finished_at_utc")
    ):
        st.warning(
            "Collectorrapport en scanhistoriek "
            "zijn niet gesynchroniseerd."
        )

    generated = paper.get("generated_utc")

    try:
        paper_age = (
            datetime.now(timezone.utc)
            - datetime_utc(generated)
        ).total_seconds() / 60

        if paper_age > 20:
            st.warning(
                "Het paper-engine-rapport is "
                "ouder dan 20 minuten."
            )

    except (TypeError, ValueError):
        st.warning(
            "Geen geldig paper-engine tijdstip."
        )

    a, b, c, d = st.columns(4)

    a.metric(
        "Laatste scan (België)",
        belgian(current.get("finished_at_utc"))
    )

    b.metric(
        "Leeftijd rapport",
        f"{max(0, age):.0f} min"
    )

    c.metric(
        "Gemeten tokens",
        int(current.get("recorded") or 0)
    )

    d.metric(
        "API-meldingen",
        int(current.get("error_count") or 0)
    )

    st.write(
        "**Laatste paper-enginerapport:**",
        belgian(generated)
    )

    if current.get("errors"):
        with st.expander(
            "⚠️ API-meldingen laatste run"
        ):
            for message in current["errors"]:
                st.write(str(message))

    rows = []

    for run in reversed(runs):
        rows.append({
            "Tijd België": belgian(
                run.get("finished_at_utc")
            ),
            "Status": run.get(
                "status", "Onbekend"
            ),
            "Tokens": int(
                run.get("recorded") or 0
            ),
            "Ontdekt": int(
                run.get("discovered") or 0
            ),
            "Nieuwe leerlabels": int(
                run.get("new_labels") or 0
            ),
            "Paper-signalen": int(
                run.get("signals") or 0
            ),
            "API-meldingen": int(
                run.get("error_count") or 0
            )
        })

    frame = pd.DataFrame(rows)

    st.subheader(
        f"Historiek — {len(rows)} opgeslagen runs"
    )

    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True
    )

    st.download_button(
        "📥 Scanhistoriek CSV",
        frame.to_csv(index=False).encode("utf-8"),
        "gta6_scan_history_v594.csv",
        "text/csv",
        key="v594_csv"
    )

    st.caption(
        "Maximaal 120 opgeslagen runs. "
        "Een fout vóór de historiekopslag kan alleen "
        "in GitHub Actions zichtbaar zijn. "
        "Een verouderd rapport helpt zulke "
        "onderbrekingen opmerken."
    )
