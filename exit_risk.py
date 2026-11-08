
"""GTA6 Radar - Unified Exit Risk Dashboard V6.17

Combineert:
- Open Position Audit
- Jupiter Exit Quotes
- Exit Risk Management
- Paper vs Jupiter
- Shadow Exit Model

VEILIGHEID:
- Alleen-lezen databasegebruik
- Geen wijzigingen aan bestaande papertrades
- Geen echte orders of transacties
- Geen automatische verkoop
- Geen wijzigingen aan strategie A/B/C/B2
- Jupiter-quotes zijn indicatief, geen gegarandeerde fills
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
import requests
import streamlit as st

from radar_quote_monitor import (
    USDC,
    USDC_DECIMALS,
    MINT_PATTERN,
    get_decimals,
    quote,
    token_amount_to_raw,
    sanitize_error,
)


# ============================================
# CONFIGURATIE
# ============================================

MAX_QUOTE_POSITIONS = 8
MAX_AGE_MIN = 12

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"

DEFAULT_TAKE_PROFIT = 30
DEFAULT_STOP_LOSS = 15
DEFAULT_MAX_HOURS = 4
DEFAULT_MIN_LIQUIDITY = 10000.0

CRITICAL_LIQUIDITY = Decimal("100")
CRITICAL_RECOVERY_PCT = Decimal("5")


# ============================================
# HULPFUNCTIES
# ============================================

def num(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (
        TypeError,
        ValueError,
        InvalidOperation,
    ):
        return None


def dt(value):
    try:
        if not value:
            return None

        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except (
        ValueError,
        TypeError,
        OverflowError,
    ):
        return None


def age_minutes(value, now):
    stamp = dt(value)

    if stamp is None:
        return None

    return (now - stamp).total_seconds() / 60


def fresh(age):
    return (
        age is not None
        and 0 <= age <= MAX_AGE_MIN
    )


def get_secret(secret, key, default=""):
    try:
        return str(secret(key) or default).strip()
    except (
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        FileNotFoundError,
    ):
        return default


def show_table(rows, filename, key):
    if not rows:
        st.info("Geen gegevens beschikbaar.")
        return

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


# ============================================
# 1. OPEN POSITIES UIT BESTAANDE DATABASE
# ============================================

def get_positions(select, now):

    trades = select("""
        SELECT
            id,
            address,
            name,
            pair_address,
            opened_at,
            entry_market,
            amount_usd,
            quantity,
            fee_rate,
            slippage_rate,
            exit_reason,
            last_mark,
            last_mark_at

        FROM paper_v542_b

        WHERE status='OPEN'

        ORDER BY opened_at, id
    """)

    positions = []

    for trade in trades:

        (
            tid,
            mint,
            name,
            pair,
            opened,
            entry,
            stake,
            quantity,
            fee,
            slip,
            reason,
            mark,
            mark_at,
        ) = trade

        latest = select("""
            SELECT
                scanned_at,
                price,
                liquidity,
                volume_m5,
                pair_address

            FROM radar_v54

            WHERE address=?

            ORDER BY scanned_at DESC, pair_address DESC

            LIMIT 1
        """, (mint,))

        if latest:
            scan, price, liquidity, volume, observed_pair = latest[0]
        else:
            scan = None
            price = None
            liquidity = None
            volume = None
            observed_pair = None

        entry = num(entry)
        stake = num(stake)
        quantity = num(quantity)
        fee = num(fee)
        slip = num(slip)
        price = num(price)
        liquidity = num(liquidity)
        volume = num(volume)
        mark = num(mark)

        scan_age = age_minutes(scan, now)
        mark_age = age_minutes(mark_at, now)

        pair_changed = bool(
            pair
            and observed_pair
            and pair != observed_pair
        )

        change_pct = None

        if (
            entry is not None
            and entry > 0
            and price is not None
            and price > 0
        ):
            change_pct = (price / entry - 1) * 100

        opened_dt = dt(opened)

        held_hours = None

        if opened_dt is not None:
            held_hours = (
                now - opened_dt
            ).total_seconds() / 3600

        # Theoretische uitstapwaarde.
        # Alleen bij recente marktinformatie.

        paper_exit = None

        if (
            quantity is not None
            and quantity > 0
            and price is not None
            and price > 0
            and fee is not None
            and slip is not None
            and 0 <= fee < 1
            and 0 <= slip < 1
            and fresh(scan_age)
            and not pair_changed
        ):
            paper_exit = (
                quantity
                * price
                * (1 - slip)
                * (1 - fee)
            )

        positions.append({
            "id": tid,
            "mint": mint,
            "name": name or "Onbekend",
            "pair": pair,
            "observed_pair": observed_pair,
            "opened": opened,
            "held_hours": held_hours,
            "entry": entry,
            "stake": stake,
            "quantity": quantity,
            "fee": fee,
            "slip": slip,
            "reason": reason,
            "scan": scan,
            "scan_age": scan_age,
            "mark_at": mark_at,
            "mark_age": mark_age,
            "liquidity": liquidity,
            "volume": volume,
            "last_price": price,
            "mark": mark,
            "change_pct": change_pct,
            "paper_exit": paper_exit,
            "pair_changed": pair_changed,
        })

    return positions


# ============================================
# 2. JUPITER VERKOOPQUOTE
# ============================================

def evaluate_quote(position, api_key, rpc, session):

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
        stake is None
        or stake <= 0
        or quantity is None
        or quantity <= 0
    ):
        result["Reden"] = "ONGELDIGE_POSITIE"
        return result

    if not api_key:
        result["Reden"] = "JUPITER_API_KEY_ONTBREEKT"
        return result

    if not rpc.startswith("https://"):
        result["Reden"] = "RPC_URL_ONGELDIG"
        return result

    try:
        decimals = get_decimals(
            session,
            mint,
            rpc,
        )

        raw_quantity = token_amount_to_raw(
            quantity,
            decimals,
        )

        response = quote(
            session,
            api_key,
            mint,
            USDC,
            raw_quantity,
        )

        if (
            response.get("error_code")
            or response.get("quote_only") is not True
        ):
            result["Reden"] = "ROUTE_ERROR"
            return result

        usdc = (
            Decimal(str(response["output_raw"]))
            / Decimal(10 ** USDC_DECIMALS)
        )

        result["Quotecontrole"] = "QUOTE_BESCHIKBAAR"

        result["Jupiter verkoopquote $"] = float(
            round(usdc, 6)
        )

        result["Jupiter P/L $"] = float(
            round(usdc - stake, 6)
        )

        result["Reden"] = (
            "INDICATIEF_GEEN_GARANTIE"
        )

    except Exception as exc:
        result["Reden"] = sanitize_error(exc)

    return result


# ============================================
# 3. QUOTE VALIDATIE
# ============================================

def valid_quote(position, sample, quote_age):

    if not isinstance(sample, dict):
        return None

    if sample.get("Trade-ID") != position["id"]:
        return None

    if sample.get("Mint") != position["mint"]:
        return None

    if sample.get("Quotecontrole") != "QUOTE_BESCHIKBAAR":
        return None

    if not fresh(quote_age):
        return None

    value = num(sample.get("Jupiter verkoopquote $"))

    if value is None or value < 0:
        return None

    return value


# ============================================
# 4. RISICOANALYSE
# ============================================

def assess_risk(position, quote_value):

    reasons = []

    liquidity = position["liquidity"]
    change = position["change_pct"]
    stake = position["stake"]

    if liquidity is None:
        reasons.append("LIQUIDITEIT_ONBEKEND")

    elif liquidity < CRITICAL_LIQUIDITY:
        reasons.append("KRITIEKE_LIQUIDITEIT")

    elif liquidity < Decimal("10000"):
        reasons.append("LIQUIDITEIT_ONDER_10000")

    if position["pair_changed"]:
        reasons.append("PAIR_GEWIJZIGD")

    if not fresh(position["scan_age"]):
        reasons.append("MARKTMETING_VEROUDERD")

    if (
        change is not None
        and change <= Decimal("-15")
    ):
        reasons.append("STOP_LOSS_OVERSCHREDEN")

    if position["reason"] == "EXIT_BLOCKED_LIQUIDITY":
        reasons.append("EXIT_GEBLOKKEERD")

    if quote_value is None:
        reasons.append("QUOTE_NIET_ACTUEEL_OF_ONBEKEND")

    elif stake is not None and stake > 0:

        recovery_pct = (
            quote_value / stake
        ) * 100

        if recovery_pct < CRITICAL_RECOVERY_PCT:
            reasons.append("QUOTE_MINDER_DAN_5PCT_INZET")

    critical = (
        "KRITIEKE_LIQUIDITEIT" in reasons
        or "QUOTE_MINDER_DAN_5PCT_INZET" in reasons
    )

    if critical:
        level = "KRITIEK"

    elif reasons:
        level = "HOOG"

    else:
        level = "OBSERVATIE"

    return level, reasons


# ============================================
# 5. PAPER VS JUPITER VERGELIJKING
# ============================================

def compare_exit(position, quote_value):

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
    }

    paper = position["paper_exit"]
    stake = position["stake"]

    if position["pair_changed"]:
        result["Beoordeling"] = "PAIR_GEWIJZIGD"
        return result

    if not fresh(position["scan_age"]):
        result["Beoordeling"] = "MARKTMETING_VEROUDERD"
        return result

    if not fresh(position["mark_age"]):
        result["Beoordeling"] = "POSITIEMARK_VEROUDERD"
        return result

    if paper is None or paper <= 0:
        result["Beoordeling"] = "PAPERWAARDE_ONGELDIG"
        return result

    if quote_value is None:
        result["Beoordeling"] = "QUOTE_ONTBREEKT"
        return result

    if stake is None or stake <= 0:
        result["Beoordeling"] = "INZET_ONGELDIG"
        return result

    difference = quote_value - paper

    result.update({
        "Paper verkoopwaarde $": float(round(paper, 6)),
        "Jupiter verkoopwaarde $": float(round(quote_value, 6)),
        "Paper P/L $": float(round(paper - stake, 6)),
        "Jupiter P/L $": float(round(quote_value - stake, 6)),
        "Verschil Jupiter - paper $": float(round(difference, 6)),
        "Jupiter / paper %": float(
            round(quote_value / paper * 100, 3)
        ),
        "Beoordeling": (
            "QUOTE_VEEL_LAGER"
            if quote_value < paper * Decimal("0.5")
            else "QUOTE_BESCHIKBAAR"
        ),
    })

    return result


# ============================================
# 6. SCHADUWTEST UITSTAPMODEL
# ============================================

def shadow_exit(
    position,
    quote_value,
    take_pct,
    stop_pct,
    max_hours,
    min_liq,
):

    signal = "ONBETROUWBARE_MARKTDATA"

    entry = position["entry"]
    price = position["last_price"]

    if (
        fresh(position["scan_age"])
        and not position["pair_changed"]
        and entry is not None
        and entry > 0
        and price is not None
        and price > 0
    ):

        change_pct = (
            price / entry - 1
        ) * 100

        if change_pct >= Decimal(str(take_pct)):
            signal = "TAKE_PROFIT"

        elif change_pct <= -Decimal(str(stop_pct)):
            signal = "STOP_LOSS"

        elif (
            position["held_hours"] is not None
            and position["held_hours"] >= max_hours
        ):
            signal = "TIME_EXIT"

        else:
            signal = "GEEN_EXIT"

    liquidity = position["liquidity"]
    stake = position["stake"]

    # Geen fictieve verkoopregistratie.

    if signal == "ONBETROUWBARE_MARKTDATA":
        status = "GEEN_BETROUWBAAR_SIGNAAL"

    elif signal == "GEEN_EXIT":
        status = "GEEN_EXIT_NODIG"

    elif quote_value is None:
        status = "QUOTE_ONTBREEKT"

    elif (
        liquidity is None
        or liquidity < Decimal(str(min_liq))
    ):
        status = "LIQUIDITEIT_ONDER_GRENS"

    else:
        status = "SCHADUWQUOTE_BESCHIKBAAR"

    # Gebruik alleen recente, pair-consistente paperwaardes.
    # Quote en paperwaarde zijn niet atomair gemeten:
    # het verschil is indicatief, geen bewezen slippage.
    paper_value = position["paper_exit"]
    paper_comparable = (
        paper_value is not None
        and paper_value > 0
        and fresh(position["mark_age"])
        and fresh(position["scan_age"])
        and not position["pair_changed"]
        and quote_value is not None
    )
    quote_vs_paper_pct = (
        (quote_value / paper_value) * 100
        if paper_comparable else None
    )
    value_gap = (
        paper_value - quote_value
        if paper_comparable else None
    )

    quote_pnl = None
    recovery_pct = None

    if (
        quote_value is not None
        and stake is not None
        and stake > 0
    ):
        quote_pnl = quote_value - stake
        recovery_pct = (quote_value / stake) * 100

    # Herstelpercentage is uitdrukkelijk een schatting op quote-basis,
    # geen uitvoerbare exit of gerealiseerde portefeuillegegevens.
    # Dit is uitsluitend een discrepantiecontrole, geen automatische
    # beslissing tot sluiten of vasthouden van een positie.
    misleading_take_profit = (
        signal == "TAKE_PROFIT"
        and quote_pnl is not None
        and quote_pnl < 0
    )

    quote_risk = (
        "QUOTE_ONBEKEND"
        if recovery_pct is None else
        "KRITIEK_ONDER_5_PCT_INZET"
        if recovery_pct < Decimal("5") else
        "ERNSTIG_ONDER_50_PCT_INZET"
        if recovery_pct < Decimal("50") else
        "QUOTE_NIET_KRITIEK"
    )

    return {
        "Trade-ID": position["id"],
        "Token": position["name"],
        "Mint": position["mint"],
        "Uitstapsignaal": signal,
        "DB-exitstatus": position["reason"] or "—",
        "Liquiditeit $": (
            float(liquidity)
            if liquidity is not None else None
        ),
        "Jupiter verkoopquote $": (
            float(quote_value)
            if quote_value is not None else None
        ),
        "Jupiter P/L $": (
            float(round(quote_pnl, 6))
            if quote_pnl is not None else None
        ),
        "Quoteherstel % inzet": (
            float(round(recovery_pct, 3))
            if recovery_pct is not None else None
        ),
        "Quote-risico": quote_risk,
        "Take-profit maar quote-verlies": misleading_take_profit,
        "Paper verkoopwaarde $": (
            float(round(paper_value, 6))
            if paper_comparable else None
        ),
        "Jupiter / paper %": (
            float(round(quote_vs_paper_pct, 2))
            if quote_vs_paper_pct is not None else None
        ),
        "Paper minus quote $": (
            float(round(value_gap, 6))
            if value_gap is not None else None
        ),
        "Grote waardeafwijking": (
            bool(quote_vs_paper_pct < Decimal("50"))
            if quote_vs_paper_pct is not None else None
        ),
        "Schaduwstatus": status,
    }


# ============================================
# 7. HOOFDDASHBOARD
# ============================================

def show_exit_risk_dashboard(select, secret):

    st.subheader(
        "Exit Risk Dashboard — Radar B"
    )

    st.caption(
        "V6.17 — Gecombineerde positiecontrole, "
        "Jupiter-verkoopquotes, risicoanalyse, "
        "papervergelijking en schaduwtest. "
        "Geen orders of databasewijzigingen."
    )

    now = datetime.now(timezone.utc)

    try:
        positions = get_positions(select, now)

    except Exception as exc:
        st.error(
            "Posities niet beschikbaar: "
            + type(exc).__name__
        )
        return

    if not positions:
        st.success(
            "Momenteel geen open Radar B-posities."
        )
        return

    # Opgeslagen quotes uit dezelfde Streamlit-sessie.

    saved = (
        st.session_state.get("exit_risk_quote_result")
        or {}
    )

    quote_age = age_minutes(
        saved.get("checked_utc"),
        now,
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
        "Exitmodel — schaduwtest",
    ])

    # ========================================
    # TAB 1 — POSITIES
    # ========================================

    with tabs[0]:

        rows = []

        for p in positions:

            rows.append({
                "Trade-ID": p["id"],
                "Token": p["name"],
                "Mint": p["mint"],
                "Exitstatus": p["reason"] or "—",
                "Liquiditeit $": (
                    float(p["liquidity"])
                    if p["liquidity"] is not None
                    else None
                ),
                "Koersverandering %": (
                    round(float(p["change_pct"]), 2)
                    if p["change_pct"] is not None
                    else None
                ),
                "Marktleeftijd min": (
                    round(p["scan_age"], 2)
                    if p["scan_age"] is not None
                    else None
                ),
                "Positiemarkleeftijd min": (
                    round(p["mark_age"], 2)
                    if p["mark_age"] is not None
                    else None
                ),
                "Open uren": (
                    round(p["held_hours"], 2)
                    if p["held_hours"] is not None
                    else None
                ),
                "Pair gewijzigd": p["pair_changed"],
            })

        st.metric(
            "Open Radar B-posities",
            len(positions),
        )

        show_table(
            rows,
            "radar_b_positions.csv",
            "v617_positions_csv",
        )

    # ========================================
    # TAB 2 — JUPITER QUOTES
    # ========================================

    with tabs[1]:

        blocked = [
            p for p in positions
            if p["reason"] == "EXIT_BLOCKED_LIQUIDITY"
        ]

        st.write(
            f"Geblokkeerde open posities: **{len(blocked)}**"
        )

        if len(blocked) > MAX_QUOTE_POSITIONS:
            st.warning(
                "Maximaal 8 posities per quotecontrole."
            )

        if st.button(
            "Controleer actuele Jupiter-verkoopquotes",
            key="v617_quote_button",
        ):

            key = get_secret(
                secret,
                "JUPITER_API_KEY",
            )

            rpc = get_secret(
                secret,
                "SOLANA_RPC_URL",
                DEFAULT_RPC,
            )

            checked = datetime.now(timezone.utc)

            output = []

            with requests.Session() as session:

                for p in blocked[:MAX_QUOTE_POSITIONS]:

                    output.append(
                        evaluate_quote(
                            p,
                            key,
                            rpc,
                            session,
                        )
                    )

            saved = {
                "checked_utc": checked.isoformat(
                    timespec="seconds"
                ),
                "result": output,
            }

            st.session_state[
                "exit_risk_quote_result"
            ] = saved

            samples = {
                row["Trade-ID"]: row
                for row in output
            }

            quote_age = age_minutes(
                saved["checked_utc"],
                datetime.now(timezone.utc),
            )

        if not saved:
            st.info(
                "Klik op de knop om actuele quotes "
                "op te vragen."
            )

        else:
            st.caption(
                "Quotes aangevraagd op "
                + str(saved.get("checked_utc"))
                + " UTC. Quotes verlopen snel."
            )

            show_table(
                saved.get("result", []),
                "radar_b_quotes.csv",
                "v617_quotes_csv",
            )

    # ========================================
    # TAB 3 — RISICOANALYSE
    # ========================================

    with tabs[2]:

        risk_rows = []

        for p in positions:

            sample = samples.get(p["id"])

            quote_value = valid_quote(
                p,
                sample,
                quote_age,
            )

            level, reasons = assess_risk(
                p,
                quote_value,
            )

            risk_rows.append({
                "Trade-ID": p["id"],
                "Token": p["name"],
                "Mint": p["mint"],
                "Risico": level,
                "Redencodes": ", ".join(reasons),
                "Liquiditeit $": (
                    float(p["liquidity"])
                    if p["liquidity"] is not None
                    else None
                ),
                "Jupiter quote $": (
                    float(quote_value)
                    if quote_value is not None
                    else None
                ),
                "Exitstatus DB": p["reason"] or "—",
            })

        critical = sum(
            row["Risico"] == "KRITIEK"
            for row in risk_rows
        )

        a, b = st.columns(2)

        a.metric(
            "Open posities",
            len(positions),
        )

        b.metric(
            "Kritieke posities",
            critical,
        )

        show_table(
            risk_rows,
            "radar_b_risk.csv",
            "v617_risk_csv",
        )

    # ========================================
    # TAB 4 — PAPER VS JUPITER
    # ========================================

    with tabs[3]:

        comparison = []

        for p in positions:

            sample = samples.get(p["id"])

            quote_value = valid_quote(
                p,
                sample,
                quote_age,
            )

            comparison.append(
                compare_exit(
                    p,
                    quote_value,
                )
            )

        valid = [
            r for r in comparison
            if r["Jupiter verkoopwaarde $"] is not None
        ]

        a, b = st.columns(2)

        a.metric(
            "Vergelijkbare posities",
            len(valid),
        )

        b.metric(
            "Niet vergelijkbaar",
            len(comparison) - len(valid),
        )

        if valid:

            paper_total = sum(
                r["Paper verkoopwaarde $"]
                for r in valid
            )

            jupiter_total = sum(
                r["Jupiter verkoopwaarde $"]
                for r in valid
            )

            difference = jupiter_total - paper_total

            st.metric(
                "Jupiter vs paper",
                f"${difference:+,.4f}",
            )

        show_table(
            comparison,
            "radar_b_comparison.csv",
            "v617_comparison_csv",
        )

        st.caption(
            "Alleen recente, vergelijkbare metingen. "
            "Een Jupiter-quote is geen uitgevoerde swap."
        )

    # ========================================
    # TAB 5 — SCHADUWTEST
    # ========================================

    with tabs[4]:

        st.write(
            "**Schaduwtest: verbeterde uitstapcontrole**"
        )

        st.caption(
            "Deze test verandert geen strategie-instellingen. "
            "De ingestelde grenzen zijn testscenario's, "
            "geen gegarandeerde uitvoeringsregels."
        )

        col1, col2 = st.columns(2)

        with col1:

            take = st.number_input(
                "Test take-profit (%)",
                min_value=1,
                max_value=500,
                value=DEFAULT_TAKE_PROFIT,
                key="v617_take",
            )

            stop = st.number_input(
                "Test stop-loss (%)",
                min_value=1,
                max_value=99,
                value=DEFAULT_STOP_LOSS,
                key="v617_stop",
            )

        with col2:

            max_hours = st.number_input(
                "Test maximale looptijd (uren)",
                min_value=1,
                max_value=72,
                value=DEFAULT_MAX_HOURS,
                key="v617_hours",
            )

            min_liq = st.number_input(
                "Test minimale liquiditeit ($)",
                min_value=0.0,
                value=DEFAULT_MIN_LIQUIDITY,
                step=1000.0,
                key="v617_liquidity",
            )

        shadow_rows = []

        for p in positions:

            sample = samples.get(p["id"])

            quote_value = valid_quote(
                p,
                sample,
                quote_age,
            )

            shadow_rows.append(
                shadow_exit(
                    p,
                    quote_value,
                    take,
                    stop,
                    max_hours,
                    min_liq,
                )
            )

        show_table(
            shadow_rows,
            "radar_b_shadow_exit.csv",
            "v617_shadow_csv",
        )

        misleading_ids = [
            row["Trade-ID"] for row in shadow_rows
            if row["Take-profit maar quote-verlies"] is True
        ]
        if misleading_ids:
            st.error(
                "MISLEIDEND TAKE-PROFIT SIGNAAL bij trade-ID's "
                + ", ".join(map(str, misleading_ids))
                + ": de actuele Jupiter-quote ligt onder de oorspronkelijke "
                "inzet, ondanks een positief koerssignaal. "
                "Geen trade wordt automatisch afgesloten."
            )

        # Sensitiviteitsanalyse op de verkoopquote (geen veronderstelde fill).
        # Alleen posities met een geldige verse quote worden meegenomen;
        # ontbrekende quotes tellen NIET als een verkoopwaarde van $0.
        quoted_rows = [
            row for row in shadow_rows
            if row["Jupiter verkoopquote $"] is not None
            and row["Jupiter P/L $"] is not None
        ]
        if quoted_rows:
            st.write("**Uitstapscenario's op basis van actuele quotes**")
            extra_haircut = st.slider(
                "Extra veiligheidsmarge op Jupiter-quote (%)",
                min_value=0,
                max_value=30,
                value=10,
                step=5,
                key="v618_exit_haircut",
            )
            scenario_rows = []
            for row in quoted_rows:
                q = Decimal(str(row["Jupiter verkoopquote $"]))
                pnl = Decimal(str(row["Jupiter P/L $"]))
                stake = q - pnl
                stressed_value = q * (
                    Decimal("1") - Decimal(str(extra_haircut)) / 100
                )
                scenario_rows.append({
                    "Trade-ID": row["Trade-ID"],
                    "Inzet $": float(round(stake, 6)),
                    "Indicatieve Jupiter-quote $": float(round(q, 6)),
                    "Indicatieve quote-P/L $": float(round(pnl, 6)),
                    "Scenario verkoopwaarde $": float(round(stressed_value, 6)),
                    "Scenario P/L $": float(round(stressed_value - stake, 6)),
                    "Liquiditeitsblokkade": (
                        row["Schaduwstatus"] == "LIQUIDITEIT_ONDER_GRENS"
                    ),
                })
            show_table(
                scenario_rows,
                "radar_b_exit_scenarios.csv",
                "v618_exit_scenarios_csv",
            )
            total_pnl = sum(
                Decimal(str(row["Scenario P/L $"])) for row in scenario_rows
            )
            st.metric(
                "Hypothetische P/L van posities met verse quote",
                f"${total_pnl:+,.4f}",
            )
            st.caption(
                "Deze P/L veronderstelt uitsluitend voor analyse dat de "
                "indicatieve quote tegen het gekozen extra waardeverlies "
                "gerealiseerd kan worden. Dat is NIET bewezen, zeker niet "
                "bij een liquiditeitsblokkade. Geen historische backtest, "
                "geen nieuwe papertrades en geen echte verkopen."
            )
        else:
            st.info(
                "Geen recente Jupiter-quotes voor de scenarioanalyse. "
                "Vernieuw de quotes in het tweede tabblad."
            )

        blocked_count = sum(
            row["Schaduwstatus"]
            == "LIQUIDITEIT_ONDER_GRENS"
            for row in shadow_rows
        )

        critical_recovery = [
            row["Trade-ID"] for row in shadow_rows
            if row["Quote-risico"] == "KRITIEK_ONDER_5_PCT_INZET"
        ]

        if critical_recovery:
            st.error(
                "Kritieke indicatieve exitwaarde (<5% van inzet) bij "
                "trade-ID's: " + ", ".join(map(str, critical_recovery))
                + ". Dit is geen bevestigde of uitgevoerde verkoop."
            )

        available_count = sum(
            row["Schaduwstatus"]
            == "SCHADUWQUOTE_BESCHIKBAAR"
            for row in shadow_rows
        )

        large_gap_count = sum(
            row["Grote waardeafwijking"] is True
            for row in shadow_rows
        )

        a, b, c = st.columns(3)

        c.metric(
            "Quote <50% van paperwaarde",
            large_gap_count,
        )

        st.metric(
            "Kritiek: quote <5% van inzet",
            len(critical_recovery),
        )

        a.metric(
            "Exit geblokkeerd door liquiditeit",
            blocked_count,
        )

        b.metric(
            "Schaduwquotes beschikbaar boven grens",
            available_count,
        )

        st.warning(
            "Een positieve paper-P/L is niet automatisch realiseerbaar: "
            "bij dunne liquiditeit kan de Jupiter-verkoopquote sterk "
            "afwijken van de getoonde marktprijs. Dit is uitsluitend "
            "een schaduwmeting; er worden geen posities gesloten."
        )

        st.info(
            "Deze schaduwtest simuleert GEEN verkoop. "
            "Hij toont uitsluitend welke bestaande "
            "uitstapsignalen door de testfilters worden "
            "tegengehouden en welke indicatieve quotes "
            "beschikbaar zijn. Historische posities "
            "blijven ongewijzigd."
        )
