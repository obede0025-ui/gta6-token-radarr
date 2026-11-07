
"""GTA6 V5.9.1: afzonderlijke papertrade-engine in testmodus.

Leest Learning Collector SQLite uitsluitend read-only.
Wijzigt geen bestaande Streamlit- of Supabase-papertrades.
"""

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAKE = 25.0
FEE = 0.01
SLIP = 0.02
MIN_LIQ = 10000.0
MIN_VOL = 250.0
MAX_POSITIONS = 3


def utc(value):
    d = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def num(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def load_state(path):
    if not path.exists():
        return {
            "version": 1,
            "cursor": None,
            "history": {},
            "traded": {"A": [], "B": []},
            "open": {"A": {}, "B": {}},
            "closed": {"A": [], "B": []},
        }

    state = json.loads(path.read_text(encoding="utf-8"))

    if state.get("version") != 1:
        raise ValueError(
            "Onbekende state-versie: niets gewijzigd"
        )

    for field in ("history", "traded", "open", "closed"):
        if field not in state:
            raise ValueError(
                "Onvolledige portfolio-state: " + field
            )

    return state


def observations(path, cursor, since):
    if not path.is_file():
        raise FileNotFoundError(
            "Learning Collector database ontbreekt: "
            + str(path)
        )

    uri = path.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as conn:
        check = conn.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

        if check != "ok":
            raise RuntimeError(
                "Collector SQLite-integriteitscontrole mislukt"
            )

        sql = """
            SELECT scanned_at, token_address,
                   pair_address, name, price_usd,
                   liquidity_usd, volume_m5,
                   buys_m5, sells_m5,
                   pair_age_hours, average_trade_usd
            FROM snapshots
            WHERE scanned_at >= ?
            ORDER BY scanned_at,
                     token_address,
                     pair_address
        """

        for row in conn.execute(sql, (since,)):
            key = list(row[:3])

            if cursor is None or key > cursor:
                yield row


def process(state, rows):
    stats = {
        "observations": 0,
        "opened": {"A": 0, "B": 0},
        "closed": {"A": 0, "B": 0},
        "signals": 0,
    }

    traded = {
        s: set(state["traded"][s])
        for s in ("A", "B")
    }

    recent = defaultdict(list, state["history"])

    for row in rows:
        (
            stamp, address, pair, name,
            price, liq, vol, buys, sells,
            age, avg
        ) = row

        if not stamp or not address or not pair:
            continue

        state["cursor"] = [
            stamp, address, pair
        ]

        stats["observations"] += 1

        price, liq, vol, age, avg = map(
            num, (price, liq, vol, age, avg)
        )

        if price is None or price <= 0:
            continue

        stamp_dt = utc(stamp)

        # Bestaande posities controleren.
        for strategy in ("A", "B"):
            position = state["open"][strategy].get(
                address
            )

            if not position:
                continue

            if position["pair"] != pair:
                continue

            change = (
                price / position["entry_market"] - 1
            )

            hours = (
                stamp_dt - utc(position["opened_at"])
            ).total_seconds() / 3600

            reason = (
                "TAKE_PROFIT"
                if change >= 0.30
                else "STOP_LOSS"
                if change <= -0.15
                else "TIME_EXIT"
                if hours >= 4
                else None
            )

            if (
                reason
                and liq is not None
                and liq >= MIN_LIQ
            ):
                proceeds = (
                    position["quantity"]
                    * price
                    * (1 - SLIP)
                    * (1 - FEE)
                )

                trade = {
                    "token": address,
                    "name": position["name"],
                    "pair": pair,
                    "opened_at": position["opened_at"],
                    "closed_at": stamp,
                    "reason": reason,
                    "pnl_usd": round(
                        proceeds - STAKE, 5
                    ),
                }

                state["closed"][strategy].append(
                    trade
                )

                del state["open"][strategy][address]

                stats["closed"][strategy] += 1

        # Historische prijs- en volumemetingen.
        key = address + ":" + pair
        history = recent[key]

        if (
            len(history) >= 2
            and vol is not None
            and vol >= MIN_VOL
        ):
            old, previous = history[-2:]

            p1, v1 = old[1], old[2]
            p2, v2 = previous[1], previous[2]

            gap1 = (
                utc(previous[0]) - utc(old[0])
            ).total_seconds() / 60

            gap2 = (
                stamp_dt - utc(previous[0])
            ).total_seconds() / 60

            valid_intervals = (
                3.5 <= gap1 <= 20
                and 3.5 <= gap2 <= 20
            )

            if (
                valid_intervals
                and p1
                and p2
                and v1 is not None
                and v2 is not None
            ):
                gain = price / p1 - 1

                signal_a = (
                    p1 < p2 < price
                    and v1 < v2 < vol
                    and gain >= 0.02
                )

                signal_b = (
                    gain >= 0.05
                    and price > p2
                )

                micro = (
                    (num(buys) or 0)
                    + (num(sells) or 0) >= 100
                    and avg is not None
                    and avg < 3
                )

                valid_market = (
                    liq is not None
                    and liq >= MIN_LIQ
                    and age is not None
                    and 0 <= age <= 24
                    and (num(buys) or 0)
                    > (num(sells) or 0)
                )

                for strategy, triggered in (
                    ("A", signal_a and not micro),
                    ("B", signal_b),
                ):
                    if not triggered:
                        continue

                    stats["signals"] += 1

                    if not valid_market:
                        continue

                    if address in traded[strategy]:
                        continue

                    if (
                        len(state["open"][strategy])
                        >= MAX_POSITIONS
                    ):
                        continue

                    effective_entry = (
                        price * (1 + SLIP)
                    )

                    quantity = (
                        STAKE * (1 - FEE)
                        / effective_entry
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

        history.append([stamp, price, vol])
        recent[key] = history[-3:]

    state["history"] = dict(recent)

    state["traded"] = {
        s: sorted(traded[s])
        for s in ("A", "B")
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
        action="store_true",
        help="Alleen later inschakelen na controles."
    )

    args = parser.parse_args()

    state_path = Path(args.state)
    state = load_state(state_path)

    now = datetime.now(timezone.utc)

    if state["cursor"] is None:
        since = (
            now - timedelta(hours=3)
        ).isoformat(timespec="seconds")
    else:
        since = state["cursor"][0]

    stats = process(
        state,
        observations(
            Path(args.db),
            state["cursor"],
            since
        )
    )

    portfolio = {}

    for strategy in ("A", "B"):
        trades = state["closed"][strategy]

        portfolio[strategy] = {
            "open": len(
                state["open"][strategy]
            ),
            "closed": len(trades),
            "realized_pnl_usd": round(
                sum(
                    trade["pnl_usd"]
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
        "generated_utc": now.isoformat(
            timespec="seconds"
        ),
        "last_processed": state["cursor"],
        "run": stats,
        "portfolio": portfolio,
        "warning": (
            "Gesimuleerde resultaten. "
            "Geen echte orders of gegarandeerde "
            "15-secondenmonitor. "
            "Afzonderlijk van Streamlit en Supabase."
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

    # Uitgeschakeld tijdens onze eerste tests.
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
