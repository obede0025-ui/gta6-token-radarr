
"""GTA6 V6.4 — A/B/C paperengine met gedetailleerde diagnostiek.

Behoudt bestaande strategieën, exits, cursor en portefeuilleversie.
Voegt enkel diagnosegegevens toe aan het JSON-rapport.
"""

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAKE = 25.0
FEE = 0.01
SLIP = 0.02
MIN_LIQ = 10000.0
MIN_VOL = 250.0
MAX_POSITIONS = 3
STRATEGIES = ("A", "B", "C")

C_MIN_PAIR_AGE = 0.0
C_MAX_PAIR_AGE = 12.0
C_MIN_GAIN = 0.015
C_MAX_GAIN = 0.15
C_VOLUME_ACCELERATION = 1.5
C_MIN_BUYS = 3


def utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def num(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def load_state(path):
    """Laad bestaande portefeuille zonder historische data te wissen."""
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("Onbekende portefeuilleversie; niets gewijzigd")

        for field in ("history", "traded", "open", "closed"):
            if not isinstance(state.get(field), dict):
                raise ValueError(f"Ongeldige portfolio-state: {field}")

        cursor = state.get("cursor")
        if cursor is not None and not (
            isinstance(cursor, list) and len(cursor) == 3
        ):
            raise ValueError("Ongeldige cursor; niets gewijzigd")
    else:
        state = {
            "version": 1,
            "cursor": None,
            "history": {},
            "traded": {},
            "open": {},
            "closed": {},
        }

    for strategy in STRATEGIES:
        state["traded"].setdefault(strategy, [])
        state["open"].setdefault(strategy, {})
        state["closed"].setdefault(strategy, [])

        if not isinstance(state["traded"][strategy], list):
            raise ValueError(f"Ongeldige traded-lijst: {strategy}")
        if not isinstance(state["open"][strategy], dict):
            raise ValueError(f"Ongeldige open-posities: {strategy}")
        if not isinstance(state["closed"][strategy], list):
            raise ValueError(f"Ongeldige gesloten trades: {strategy}")

    return state


def observations(path, cursor, since):
    """Lees uitsluitend nieuwe snapshots na de opgeslagen cursor."""
    if not path.is_file():
        raise FileNotFoundError(f"Learning database ontbreekt: {path}")

    with sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True,
    ) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite-integriteitscontrole mislukt")

        rows = conn.execute(
            """SELECT scanned_at, token_address, pair_address, name,
                      price_usd, liquidity_usd, volume_m5,
                      buys_m5, sells_m5, pair_age_hours,
                      average_trade_usd
               FROM snapshots
               WHERE scanned_at >= ?
               ORDER BY scanned_at, token_address, pair_address""",
            (since,),
        )

        for row in rows:
            if cursor is None or list(row[:3]) > cursor:
                yield row


def recent_pair_history(history, stamp):
    """Originele voorwaarde: intervallen van 3,5 tot 20 minuten."""
    if not history:
        return []

    result = []
    next_dt = utc(stamp)

    for old in reversed(history):
        try:
            delta = (
                next_dt - utc(old[0])
            ).total_seconds() / 60
        except (ValueError, TypeError):
            break

        if not 3.5 <= delta <= 20:
            break

        result.insert(0, old)
        next_dt = utc(old[0])

    return result


def _spacing_reason(history, stamp):
    """Classificeer het interval met de vorige meting."""
    if not history:
        return "geen_vorige_meting"

    try:
        gap = (
            utc(stamp) - utc(history[-1][0])
        ).total_seconds() / 60
    except (ValueError, TypeError, IndexError):
        return "ongeldige_meettijd"

    if gap < 3.5:
        return "interval_te_kort"
    if gap > 20:
        return "interval_te_lang"
    return "interval_geldig"


def process(state, rows):
    # Bestaande rapportvelden blijven behouden.
    stats = {
        "observations": 0,
        "opened": {s: 0 for s in STRATEGIES},
        "closed": {s: 0 for s in STRATEGIES},
        "signals": 0,
        "signals_by_strategy": {s: 0 for s in STRATEGIES},
        "rejected": Counter(),
    }

    # Nieuwe, alleen-informatieve diagnostiek.
    by_strategy = {s: Counter() for s in STRATEGIES}
    spacing = Counter()
    market_filters = Counter()

    traded = {
        s: set(state["traded"][s])
        for s in STRATEGIES
    }
    recent = defaultdict(list, state["history"])

    for row in rows:
        (
            stamp, address, pair, name, price,
            liq, vol, buys, sells, age, avg
        ) = row

        if not stamp or not address or not pair:
            continue

        state["cursor"] = [stamp, address, pair]
        stats["observations"] += 1

        price, liq, vol, age, avg = map(
            num, (price, liq, vol, age, avg)
        )
        buys = num(buys) or 0
        sells = num(sells) or 0

        if price is None or price <= 0:
            stats["rejected"]["invalid_price"] += 1
            for strategy in STRATEGIES:
                by_strategy[strategy]["ongeldige_prijs"] += 1
            continue

        stamp_dt = utc(stamp)

        # =========================================
        # BESTAANDE EXITREGELS — ONGEWIJZIGD
        # =========================================

        for strategy in STRATEGIES:
            position = state["open"][strategy].get(address)

            if not position or position.get("pair") != pair:
                continue

            entry = num(position.get("entry_market"))
            quantity = num(position.get("quantity"))

            if not entry or entry <= 0 or quantity is None:
                stats["rejected"]["invalid_open_position"] += 1
                continue

            change = price / entry - 1
            hours = (
                stamp_dt - utc(position["opened_at"])
            ).total_seconds() / 3600

            reason = (
                "TAKE_PROFIT" if change >= 0.30 else
                "STOP_LOSS" if change <= -0.15 else
                "TIME_EXIT" if hours >= 4 else None
            )

            if reason and liq is not None and liq >= MIN_LIQ:
                proceeds = (
                    quantity * price * (1 - SLIP) * (1 - FEE)
                )

                state["closed"][strategy].append({
                    "token": address,
                    "name": position.get("name", "Onbekend"),
                    "pair": pair,
                    "opened_at": position["opened_at"],
                    "closed_at": stamp,
                    "reason": reason,
                    "pnl_usd": round(proceeds - STAKE, 5),
                })

                del state["open"][strategy][address]
                stats["closed"][strategy] += 1

        # =========================================
        # MEETGESCHIEDENIS EN INTERVALDIAGNOSE
        # =========================================

        key = address + ":" + pair
        history = recent[key]

        spacing[_spacing_reason(history, stamp)] += 1
        consecutive = recent_pair_history(
            history, stamp_dt.isoformat()
        )

        liquidity_ok = liq is not None and liq >= MIN_LIQ
        volume_ok = vol is not None and vol >= MIN_VOL
        age_ok = age is not None and 0 <= age <= 24
        buyers_ok = buys > sells

        micro = (
            buys + sells >= 100
            and avg is not None
            and avg < 3
        )

        valid_market = (
            liquidity_ok
            and volume_ok
            and age_ok
            and buyers_ok
        )

        # Meerdere marktfilters kunnen tegelijk falen.
        if not liquidity_ok:
            market_filters[
                "liquiditeit_te_laag_of_onbekend"
            ] += 1

        if not volume_ok:
            market_filters[
                "volume_5m_te_laag_of_onbekend"
            ] += 1

        if not age_ok:
            market_filters["paarleeftijd_ongeldig"] += 1

        if not buyers_ok:
            market_filters[
                "buys_niet_hoger_dan_sells"
            ] += 1

        if micro:
            market_filters["microtransacties"] += 1

        # =========================================
        # BESTAANDE STRATEGIEËN — ONGEWIJZIGD
        # =========================================

        a_signal = False
        b_signal = False
        c_signal = False

        ab_reference_ok = False
        c_reference_ok = False

        # Strategie A en B: drie metingen.
        if len(consecutive) >= 2 and volume_ok:
            old, previous = consecutive[-2:]

            p1, v1 = num(old[1]), num(old[2])
            p2, v2 = num(previous[1]), num(previous[2])

            if (
                p1 and p1 > 0
                and p2 and p2 > 0
                and v1 is not None
                and v2 is not None
            ):
                ab_reference_ok = True
                gain = price / p1 - 1

                a_signal = (
                    p1 < p2 < price
                    and v1 < v2 < vol
                    and gain >= 0.02
                )

                b_signal = (
                    gain >= 0.05
                    and price > p2
                )

        # Strategie C: twee metingen.
        if len(consecutive) >= 1 and volume_ok:
            previous = consecutive[-1]

            prior_price = num(previous[1])
            prior_volume = num(previous[2])

            if (
                prior_price and prior_price > 0
                and prior_volume and prior_volume > 0
            ):
                c_reference_ok = True
                gain_c = price / prior_price - 1

                c_signal = (
                    C_MIN_GAIN <= gain_c <= C_MAX_GAIN
                    and vol >= (
                        prior_volume * C_VOLUME_ACCELERATION
                    )
                    and buys >= C_MIN_BUYS
                    and not micro
                    and age is not None
                    and C_MIN_PAIR_AGE <= age <= C_MAX_PAIR_AGE
                )

        if not any((a_signal, b_signal, c_signal)):
            stats["rejected"]["no_momentum_signal"] += 1

        # =========================================
        # PAPERTRADES + EXTRA DIAGNOSTIEK
        # =========================================

        for strategy, triggered in (
            ("A", a_signal and not micro),
            ("B", b_signal),
            ("C", c_signal),
        ):
            if not triggered:
                required_history = (
                    1 if strategy == "C" else 2
                )

                reference_ok = (
                    c_reference_ok
                    if strategy == "C"
                    else ab_reference_ok
                )

                if len(consecutive) < required_history:
                    diagnosis = (
                        "te_weinig_opeenvolgende_metingen"
                    )
                elif not volume_ok:
                    diagnosis = "volume_5m_te_laag"
                elif not reference_ok:
                    diagnosis = "ongeldige_referentiemetingen"
                elif strategy == "A" and a_signal and micro:
                    diagnosis = "microtransactiefilter"
                else:
                    diagnosis = (
                        "momentumvoorwaarden_niet_gehaald"
                    )

                by_strategy[strategy][diagnosis] += 1
                continue

            stats["signals"] += 1
            stats["signals_by_strategy"][strategy] += 1

            if not valid_market:
                stats["rejected"][
                    "market_safety_filters"
                ] += 1

                if not liquidity_ok:
                    diagnosis = (
                        "signaal_geblokkeerd_liquiditeit"
                    )
                elif not volume_ok:
                    diagnosis = (
                        "signaal_geblokkeerd_volume"
                    )
                elif not age_ok:
                    diagnosis = (
                        "signaal_geblokkeerd_paarleeftijd"
                    )
                else:
                    diagnosis = (
                        "signaal_geblokkeerd_buys_sells"
                    )

                by_strategy[strategy][diagnosis] += 1
                continue

            if address in traded[strategy]:
                stats["rejected"]["already_traded"] += 1
                by_strategy[strategy][
                    "al_eerder_verhandeld"
                ] += 1
                continue

            if len(state["open"][strategy]) >= MAX_POSITIONS:
                stats["rejected"]["max_positions"] += 1
                by_strategy[strategy][
                    "maximum_open_posities"
                ] += 1
                continue

            effective_entry = price * (1 + SLIP)
            quantity = (
                STAKE * (1 - FEE) / effective_entry
            )

            state["open"][strategy][address] = {
                "name": name or "Onbekend",
                "pair": pair,
                "opened_at": stamp,
                "entry_market": price,
                "quantity": quantity,
            }

            traded[strategy].add(address)
            stats["opened"][strategy] += 1

            by_strategy[strategy][
                "papertrade_geopend"
            ] += 1

        # Bestaande limiet: maximaal 3 metingen per pair.
        history.append([stamp, price, vol])
        recent[key] = history[-3:]

    state["history"] = dict(recent)
    state["traded"] = {
        s: sorted(traded[s])
        for s in STRATEGIES
    }

    stats["rejected"] = dict(stats["rejected"])

    stats["diagnostics"] = {
        "by_strategy": {
            s: dict(by_strategy[s])
            for s in STRATEGIES
        },
        "spacing": dict(spacing),
        "market_filters": dict(market_filters),
        "note": (
            "Eén primaire diagnose per strategie per "
            "verwerkte meting. Marktfilters kunnen "
            "overlappen. Papertrades blijven simulaties."
        ),
    }

    return stats


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        default="data/learning.db"
    )
    parser.add_argument(
        "--state",
        default="data/v591/portfolio.json"
    )
    parser.add_argument(
        "--output",
        default="data/v591/preview.json"
    )
    parser.add_argument(
        "--save-state",
        action="store_true"
    )

    args = parser.parse_args()

    state_path = Path(args.state)
    state = load_state(state_path)

    created = datetime.now(timezone.utc)

    since = (
        (
            created - timedelta(hours=3)
        ).isoformat(timespec="seconds")
        if state["cursor"] is None
        else state["cursor"][0]
    )

    stats = process(
        state,
        observations(
            Path(args.db),
            state["cursor"],
            since
        )
    )

    portfolio = {}

    for strategy in STRATEGIES:
        trades = state["closed"][strategy]

        portfolio[strategy] = {
            "open": len(state["open"][strategy]),
            "closed": len(trades),
            "realized_pnl_usd": round(
                sum(
                    float(trade["pnl_usd"])
                    for trade in trades
                ),
                4
            ),
        }

    result = {
        "mode": (
            "ISOLATED_PAPER_STATE"
            if args.save_state
            else "ISOLATED_PAPER_PREVIEW"
        ),
        "generated_utc": created.isoformat(
            timespec="seconds"
        ),
        "last_processed": state["cursor"],
        "run": stats,
        "portfolio": portfolio,
        "warning": (
            "Gesimuleerde resultaten; geen echte orders, "
            "geen gegarandeerde 15-secondenmonitor en "
            "geen bevestiging van verkoopbaarheid. "
            "A/B/C gescheiden van Streamlit en Supabase."
        ),
    }

    output = Path(args.output)
    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    if args.save_state:
        state_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        temporary = state_path.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                state,
                indent=2,
                ensure_ascii=False
            ) + "\n",
            encoding="utf-8"
        )

        temporary.replace(state_path)

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
