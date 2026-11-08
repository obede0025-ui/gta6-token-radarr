
"""Unified Radar B exit-risk dashboard, read-only (V6.13-V6.16).

No database writes, simulated exits, order signing, or live trades.
Uses the existing Radar B portfolio and market observations.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
import requests
import streamlit as st

from radar_quote_monitor import (
    USDC, USDC_DECIMALS, MINT_PATTERN, get_decimals,
    quote, token_amount_to_raw, sanitize_error,
)

MAX_QUOTE_POSITIONS = 8
MAX_AGE_MIN = 12
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


def num(x):
    try:
        value = Decimal(str(x))
        return value if value.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def dt(x):
    try:
        if not x:
            return None
        value = datetime.fromisoformat(
            str(x).replace("Z", "+00:00")
        )
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def age_minutes(x, now):
    timestamp = dt(x)
    return (
        (now - timestamp).total_seconds() / 60
        if timestamp else None
    )


def fresh(age):
    return age is not None and 0 <= age <= MAX_AGE_MIN


def get_positions(select, now):
    rows = select("""
        SELECT id,address,name,pair_address,opened_at,
               entry_market,amount_usd,quantity,fee_rate,
               slippage_rate,exit_reason,last_mark,last_mark_at
        FROM paper_v542_b
        WHERE status='OPEN'
        ORDER BY opened_at,id
    """)

    output = []

    for row in rows:
        (
            tid, mint, name, pair, opened, entry, stake,
            quantity, fee, slip, reason, mark, mark_at
        ) = row

        observations = select("""
            SELECT scanned_at,price,liquidity,volume_m5,
                   pair_address
            FROM radar_v54
            WHERE address=?
            ORDER BY scanned_at DESC,pair_address DESC
            LIMIT 1
        """, (mint,))

        scan, price, liquidity, volume, observed_pair = (
            observations[0] if observations else (None,) * 5
        )

        decisions = select("""
            SELECT scan_at,outcome,reason
            FROM v542_decisions
            WHERE address=? AND strategy='B'
            ORDER BY scan_at DESC
            LIMIT 1
        """, (mint,))

        (
            entry, stake, quantity, fee, slip,
            price, liquidity, mark
        ) = map(
            num,
            (
                entry, stake, quantity, fee, slip,
                price, liquidity, mark
            )
        )

        change = (
            (price / entry - 1) * 100
            if entry and entry > 0 and price is not None
            else None
        )

        pair_changed = bool(
            pair and observed_pair and pair != observed_pair
        )

        paper = None

        if (
            quantity is not None
            and quantity > 0
            and price is not None
            and price > 0
            and fee is not None
            and slip is not None
            and 0 <= fee < 1
            and 0 <= slip < 1
            and fresh(age_minutes(scan, now))
            and not pair_changed
        ):
            paper = (
                quantity * price * (1 - slip) * (1 - fee)
            )

        output.append({
            "id": tid,
            "mint": mint,
            "name": name or "Onbekend",
            "pair": pair,
            "opened": opened,
            "entry": entry,
            "stake": stake,
            "quantity": quantity,
            "fee": fee,
            "slip": slip,
            "reason": reason,
            "scan": scan,
            "scan_age": age_minutes(scan, now),
            "mark_at": mark_at,
            "mark_age": age_minutes(mark_at, now),
            "liquidity": liquidity,
            "volume": num(volume),
            "last_price": price,
            "mark": mark,
            "change_pct": change,
            "paper_exit": paper,
            "pair_changed": pair_changed,
            "last_decision": (
                decisions[0] if decisions else None
            ),
        })

    return output


def evaluate_quote(position, key, rpc, session):
    result = {
        "Trade-ID": position["id"],
        "Token": position["name"],
        "Mint": position["mint"],
        "Quotecontrole": "QUOTE_ONBESCHIKBAAR",
        "Reden": "",
        "Jupiter verkoopquote $": None,
        "Jupiter P/L $": None,
    }

    mint = position["mint"]
    stake = position["stake"]
    quantity = position["quantity"]

    if not MINT_PATTERN.fullmatch(str(mint or "")):
        result["Reden"] = "ONGELDIGE_MINT"
        return result

    if (
        stake is None or stake <= 0
        or quantity is None or quantity <= 0
    ):
        result["Reden"] = "ONGELDIGE_POSITIE"
        return result

    if not key:
        result["Reden"] = "JUPITER_API_KEY_ONTBREEKT"
        return result

    if not rpc.startswith("https://"):
        result["Reden"] = "RPC_URL_ONGELDIG"
        return result

    try:
        decimals = get_decimals(session, mint, rpc)
        amount = token_amount_to_raw(quantity, decimals)

        response = quote(
            session, key, mint, USDC, amount
        )

        if (
            response.get("error_code")
            or response.get("quote_only") is not True
        ):
            result["Reden"] = "ROUTE_ERROR"
            return result

        amount_usdc = (
            Decimal(str(response["output_raw"]))
            / Decimal(10 ** USDC_DECIMALS)
        )

        result["Quotecontrole"] = "QUOTE_BESCHIKBAAR"
        result["Jupiter verkoopquote $"] = float(
            round(amount_usdc, 6)
        )
        result["Jupiter P/L $"] = float(
            round(amount_usdc - stake, 6)
        )
        result["Reden"] = "INDICATIEF_GEEN_GARANTIE"

    except Exception as exc:
        result["Reden"] = sanitize_error(exc)

    return result


def assess(position, sample, quote_age):
    reasons = []
    liquidity = position["liquidity"]

    if liquidity is None:
        reasons.append("LIQUIDITEIT_ONBEKEND")
    elif liquidity < 100:
        reasons.append("KRITIEKE_LIQUIDITEIT_ONDER_100")
    elif liquidity < 10000:
        reasons.append("LIQUIDITEIT_ONDER_10000")

    if position["pair_changed"]:
        reasons.append("PAIR_GEWIJZIGD")

    if not fresh(position["scan_age"]):
        reasons.append("MARKTMETING_VEROUDERD")

    change = position["change_pct"]

    if change is not None and change <= -15:
        reasons.append(
            "STOP_LOSS_OVERSCHREDEN_OP_LAATSTE_MARKTPRIJS"
        )

    valid = (
        sample is not None
        and sample.get("Mint") == position["mint"]
        and sample.get("Quotecontrole")
        == "QUOTE_BESCHIKBAAR"
        and fresh(quote_age)
    )

    quote_value = (
        num(sample.get("Jupiter verkoopquote $"))
        if valid else None
    )

    if quote_value is None:
        reasons.append(
            "JUPITER_QUOTE_NIET_ACTUEEL_OF_ONBEKEND"
        )
    elif (
        position["stake"] is not None
        and quote_value < position["stake"] * Decimal("0.05")
    ):
        reasons.append("QUOTE_MINDER_DAN_5PCT_INZET")

    critical = (
        "KRITIEKE_LIQUIDITEIT_ONDER_100" in reasons
        or "QUOTE_MINDER_DAN_5PCT_INZET" in reasons
    )

    level = (
        "KRITIEK" if critical
        else "HOOG" if reasons
        else "OBSERVATIE"
    )

    return level, reasons, quote_value


def compare(position, sample, quote_age):
    result = {
        "Trade-ID": position["id"],
        "Token": position["name"],
        "Mint": position["mint"],
        "Paper verkoopwaarde $": None,
        "Jupiter verkoopwaarde $": None,
        "Paper P/L $": None,
        "Jupiter P/L $": None,
        "Verschil Jupiter - paper $": None,
        "Jupiter / paper %": None,
        "Beoordeling": "NIET_VERGELIJKBAAR",
        "Reden": "",
    }

    if (
        not sample
        or sample.get("Mint") != position["mint"]
        or sample.get("Quotecontrole")
        != "QUOTE_BESCHIKBAAR"
    ):
        result["Reden"] = "GEEN_QUOTE_VOOR_DEZE_POSITIE"
        return result

    if not fresh(quote_age):
        result["Reden"] = "QUOTE_VEROUDERD"
        return result

    if position["pair_changed"]:
        result["Reden"] = "PAIR_GEWIJZIGD"
        return result

    if (
        not fresh(position["mark_age"])
        or not fresh(position["scan_age"])
    ):
        result["Reden"] = (
            "MARKTMETING_OF_POSITIEMARK_VEROUDERD"
        )
        return result

    paper = position["paper_exit"]
    usdc = num(sample.get("Jupiter verkoopquote $"))
    pnl = num(sample.get("Jupiter P/L $"))
    stake = position["stake"]

    if (
        paper is None
        or stake is None
        or usdc is None
        or usdc < 0
        or pnl is None
        or abs((usdc - stake) - pnl) > Decimal("0.02")
    ):
        result["Reden"] = "ONGELDIGE_OF_INCONSISTENTE_WAARDEN"
        return result

    result.update({
        "Paper verkoopwaarde $": float(round(paper, 6)),
        "Jupiter verkoopwaarde $": float(round(usdc, 6)),
        "Paper P/L $": float(round(paper - stake, 6)),
        "Jupiter P/L $": float(round(usdc - stake, 6)),
        "Verschil Jupiter - paper $": float(
            round(usdc - paper, 6)
        ),
        "Jupiter / paper %": (
            float(round(usdc / paper * 100, 3))
            if paper > 0 else None
        ),
        "Beoordeling": (
            "QUOTE_VEEL_LAGER"
            if paper > 0 and usdc < paper * Decimal("0.5")
            else "QUOTE_BESCHIKBAAR"
        ),
        "Reden": "INDICATIEF_GEEN_GEGARANDEERDE_FILL",
    })

    return result


def table(rows, filename, key):
    frame = pd.DataFrame(rows)

    st.dataframe(
        frame,
        hide_index=True,
        use_container_width=True,
    )

    st.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        key=key,
    )


def show_exit_risk_dashboard(select, secret):
    st.subheader("Exit Risk Dashboard — Radar B")

    st.caption(
        "Gecombineerde V6.13-V6.16. Alleen-lezen: "
        "geen orders, databasewijzigingen of "
        "aangepaste papertrades."
    )

    now = datetime.now(timezone.utc)

    try:
        positions = get_positions(select, now)
    except Exception as exc:
        st.error(
            "Lokale posities niet beschikbaar: "
            + type(exc).__name__
        )
        return

    if not positions:
        st.success("Geen open Radar B-posities.")
        return

    saved = (
        st.session_state.get("exit_risk_quote_result") or {}
    )

    saved_age = age_minutes(
        saved.get("checked_utc"), now
    )

    samples = {
        row.get("Trade-ID"): row
        for row in saved.get("result", [])
        if isinstance(row, dict)
        and row.get("Trade-ID") is not None
    }

    tabs = st.tabs([
        "Posities",
        "Jupiter-verkoopquotes",
        "Risicoanalyse",
        "Paper vs Jupiter",
    ])

    with tabs[0]:
        rows = []

        for position in positions:
            rows.append({
                "Trade-ID": position["id"],
                "Token": position["name"],
                "Mint": position["mint"],
                "Exitreden": position["reason"] or "—",
                "Liquiditeit $": (
                    float(position["liquidity"])
                    if position["liquidity"] is not None
                    else None
                ),
                "Koersverandering %": (
                    round(float(position["change_pct"]), 2)
                    if position["change_pct"] is not None
                    else None
                ),
                "Marktleeftijd min": (
                    round(position["scan_age"], 2)
                    if position["scan_age"] is not None
                    else None
                ),
                "Markleeftijd min": (
                    round(position["mark_age"], 2)
                    if position["mark_age"] is not None
                    else None
                ),
                "Pair gewijzigd": position["pair_changed"],
                "Laatste B-beslissing": (
                    position["last_decision"]
                ),
            })

        st.metric("Open posities", len(positions))

        table(
            rows,
            "radar_b_positions.csv",
            "exit_risk_positions_csv",
        )

    with tabs[1]:
        blocked = [
            position for position in positions
            if position["reason"] == "EXIT_BLOCKED_LIQUIDITY"
        ]

        st.write(
            f"Geblokkeerde open posities: **{len(blocked)}**"
        )

        if len(blocked) > MAX_QUOTE_POSITIONS:
            st.warning(
                "Maximaal 8 posities per controle "
                "om API-belasting te beperken."
            )

        if st.button(
            "Controleer actuele Jupiter-verkoopquotes",
            key="exit_risk_quote_button",
        ):
            try:
                key = str(
                    secret("JUPITER_API_KEY") or ""
                ).strip()

                rpc = str(
                    secret("SOLANA_RPC_URL") or DEFAULT_RPC
                ).strip()

            except (
                KeyError,
                ValueError,
                TypeError,
                AttributeError,
                FileNotFoundError,
            ):
                key = ""
                rpc = DEFAULT_RPC

            checked = datetime.now(timezone.utc)

            with requests.Session() as session:
                quotes = [
                    evaluate_quote(
                        position, key, rpc, session
                    )
                    for position in blocked[:MAX_QUOTE_POSITIONS]
                ]

            saved = {
                "checked_utc": checked.isoformat(
                    timespec="seconds"
                ),
                "result": quotes,
            }

            st.session_state[
                "exit_risk_quote_result"
            ] = saved

            samples = {
                row["Trade-ID"]: row
                for row in quotes
            }

            saved_age = age_minutes(
                saved["checked_utc"],
                datetime.now(timezone.utc),
            )

        if not saved:
            st.info(
                "Klik op de knop voor actuele quotes. "
                "Secrets blijven verborgen."
            )
        else:
            st.caption(
                "Quotes aangevraagd op "
                + str(saved.get("checked_utc"))
                + " UTC. Quotes verlopen snel."
            )

            table(
                saved.get("result", []),
                "radar_b_quotes.csv",
                "exit_risk_quotes_csv",
            )

    with tabs[2]:
        rows = []

        for position in positions:
            status, reasons, quote_value = assess(
                position,
                samples.get(position["id"]),
                saved_age,
            )

            rows.append({
                "Trade-ID": position["id"],
                "Token": position["name"],
                "Mint": position["mint"],
                "Risico": status,
                "Redencodes": ", ".join(reasons),
                "Exitstatus DB": position["reason"] or "—",
                "Liquiditeit $": (
                    float(position["liquidity"])
                    if position["liquidity"] is not None
                    else None
                ),
                "Jupiter quote $": (
                    float(quote_value)
                    if quote_value is not None
                    else None
                ),
                "Quoteleeftijd min": (
                    round(saved_age, 2)
                    if quote_value is not None
                    else None
                ),
            })

        st.metric(
            "Kritiek risico",
            sum(
                row["Risico"] == "KRITIEK"
                for row in rows
            ),
        )

        table(
            rows,
            "radar_b_risks.csv",
            "exit_risk_risks_csv",
        )

    with tabs[3]:
        rows = [
            compare(
                position,
                samples.get(position["id"]),
                saved_age,
            )
            for position in positions
        ]

        valid = [
            row for row in rows
            if row["Jupiter verkoopwaarde $"] is not None
        ]

        col1, col2 = st.columns(2)

        col1.metric(
            "Vergelijkbare posities",
            len(valid),
        )

        col2.metric(
            "Niet vergelijkbaar",
            len(rows) - len(valid),
        )

        if valid:
            paper = sum(
                row["Paper verkoopwaarde $"]
                for row in valid
            )

            jupiter = sum(
                row["Jupiter verkoopwaarde $"]
                for row in valid
            )

            st.metric(
                "Jupiter vs paper (zelfde posities)",
                f"${jupiter - paper:+,.4f}",
            )

        table(
            rows,
            "radar_b_exit_comparison.csv",
            "exit_risk_comparison_csv",
        )

        st.caption(
            "Geen daadwerkelijke verkoop of historische "
            "reconstructie. Jupiter-quotes zijn indicatief. "
            "Netwerk- en prioriteitskosten zijn niet inbegrepen."
        )
