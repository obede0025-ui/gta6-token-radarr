
"""GTA6 V5.9 shadow paper engine.

Reads collector snapshots and writes a report only.
No live trading. Does not modify existing papertrades.
"""

import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

INPUT = Path(os.getenv("LEARNING_DB", "data/learning.db"))
OUTPUT = Path(
    os.getenv("V59_REPORT", "data/v59_shadow_report.json")
)

STAKE = 25.0
FEE = 0.01
SLIPPAGE = 0.02
MIN_LIQUIDITY = 10000
MIN_VOLUME = 250
MAX_OPEN = 3
MAX_HOURS = 4


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def epoch(value):
    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load():
    if not INPUT.is_file():
        raise RuntimeError(
            f"Collector database ontbreekt: {INPUT}"
        )

    uri = INPUT.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(
        uri, uri=True, timeout=30
    ) as conn:

        if conn.execute(
            "PRAGMA quick_check"
        ).fetchone()[0] != "ok":
            raise RuntimeError("SQLite-integriteit mislukt")

        rows = conn.execute("""
            SELECT
                token_address,
                pair_address,
                scanned_at,
                name,
                price_usd,
                liquidity_usd,
                volume_m5,
                buys_m5,
                sells_m5,
                pair_age_hours,
                average_trade_usd
            FROM snapshots
            ORDER BY scanned_at ASC,
                     token_address,
                     pair_address
        """).fetchall()

    return rows


def run(rows):
    histories = defaultdict(list)

    positions = {
        "A": {},
        "B": {}
    }

    traded = {
        "A": set(),
        "B": set()
    }

    closed = {
        "A": [],
        "B": []
    }

    signals = []
    events = 0
    latest_stamp = None

    for raw in rows:
        (
            address, pair, stamp, name,
            price, liquidity, volume,
            buys, sells, age, avg
        ) = raw

        if not address or not pair:
            continue

        try:
            ts = epoch(stamp)
        except (ValueError, TypeError):
            continue

        latest_stamp = max(
            latest_stamp or ts, ts
        )

        price, liquidity, volume, age, avg = map(
            number,
            (price, liquidity, volume, age, avg)
        )

        buys = number(buys) or 0
        sells = number(sells) or 0

        if price is None or price <= 0:
            continue

        events += 1

        # Process existing virtual positions first.
        for strategy in ("A", "B"):
            current = positions[strategy].get(address)

            if current is None:
                continue

            if current["pair"] != pair:
                continue

            change = (
                price / current["entry_market"] - 1
            )

            duration = (
                ts - current["entry_epoch"]
            ) / 3600

            reason = (
                "TAKE_PROFIT"
                if change >= 0.30
                else "STOP_LOSS"
                if change <= -0.15
                else "TIME_EXIT"
                if duration >= MAX_HOURS
                else None
            )

            if (
                reason
                and liquidity is not None
                and liquidity >= MIN_LIQUIDITY
            ):
                proceeds = (
                    current["quantity"]
                    * price
                    * (1 - SLIPPAGE)
                    * (1 - FEE)
                )

                closed[strategy].append({
                    "token": address,
                    "name": current["name"],
                    "pair": pair,
                    "opened_at": current["opened_at"],
                    "closed_at": stamp,
                    "entry_market": current["entry_market"],
                    "exit_market": price,
                    "reason": reason,
                    "pnl_usd": round(
                        proceeds - STAKE, 5
                    )
                })

                del positions[strategy][address]

        key = (address, pair)
        previous = histories[key]

        suitable = [
            r for r in previous[-8:]
            if 0 < ts - r[0] <= 2400
        ]

        if len(suitable) >= 2:
            older, recent = suitable[-2:]

            gap1 = (
                recent[0] - older[0]
            ) / 60

            gap2 = (
                ts - recent[0]
            ) / 60

            valid = (
                3.5 <= gap1 <= 20
                and 3.5 <= gap2 <= 20
            )

            p1, v1 = older[1], older[2]
            p2, v2 = recent[1], recent[2]

            if (
                valid
                and volume is not None
                and volume >= MIN_VOLUME
                and v1 is not None
                and v2 is not None
            ):
                change = price / p1 - 1

                a = (
                    p1 < p2 < price
                    and change >= 0.02
                    and v1 < v2 < volume
                )

                b = (
                    change >= 0.05
                    and price > p2
                )

                safe = (
                    liquidity is not None
                    and liquidity >= MIN_LIQUIDITY
                    and age is not None
                    and age <= 24
                    and buys > sells
                )

                micro = (
                    buys + sells >= 100
                    and avg is not None
                    and avg < 3
                )

                for strategy, trigger in [
                    ("A", a and not micro),
                    ("B", b)
                ]:
                    if not trigger:
                        continue

                    signals.append({
                        "strategy": strategy,
                        "token": address,
                        "name": name,
                        "at": stamp,
                        "qualified": bool(safe),
                        "pair": pair
                    })

                    if (
                        not safe
                        or address in traded[strategy]
                        or len(positions[strategy]) >= MAX_OPEN
                    ):
                        continue

                    entry_effective = (
                        price * (1 + SLIPPAGE)
                    )

                    quantity = (
                        STAKE * (1 - FEE)
                        / entry_effective
                    )

                    positions[strategy][address] = {
                        "name": name,
                        "pair": pair,
                        "opened_at": stamp,
                        "entry_epoch": ts,
                        "entry_market": price,
                        "quantity": quantity
                    }

                    traded[strategy].add(address)

        previous.append(
            (ts, price, volume)
        )

        if len(previous) > 8:
            del previous[:-8]

    result = {}

    for strategy in ("A", "B"):
        profits = [
            x["pnl_usd"]
            for x in closed[strategy]
        ]

        result[strategy] = {
            "simulated_entries": len(
                traded[strategy]
            ),
            "closed_count": len(profits),
            "open_count": len(
                positions[strategy]
            ),
            "realized_simulated_pnl_usd": round(
                sum(profits), 4
            ),
            "wins": sum(
                p > 0 for p in profits
            ),
            "recent_closed": closed[strategy][-20:],
            "open_positions": [
                {
                    "token": address,
                    "name": p["name"],
                    "opened_at": p["opened_at"],
                    "entry_market": p["entry_market"],
                    "pair": p["pair"]
                }
                for address, p
                in positions[strategy].items()
            ]
        }

    return {
        "mode": "SHADOW_ONLY",
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "last_observation_utc": (
            datetime.fromtimestamp(
                latest_stamp, timezone.utc
            ).isoformat()
            if latest_stamp else None
        ),
        "observations_processed": events,
        "strategy": result,
        "recent_signals": signals[-30:],
        "limitations": (
            "Historical collector replay only. "
            "No live orders, no actual Streamlit trades. "
            "Missing prices can delay simulated exits. "
            "Fills, fees and slippage are assumptions. "
            "No guaranteed stop-loss or 15-second monitoring."
        )
    }


if __name__ == "__main__":
    result = run(load())

    OUTPUT.parent.mkdir(
        parents=True, exist_ok=True
    )

    OUTPUT.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    print(
        json.dumps({
            "mode": result["mode"],
            "observations_processed":
                result["observations_processed"],
            "last_observation_utc":
                result["last_observation_utc"]
        })
    )

    for strategy, summary in result["strategy"].items():
        print(
            strategy,
            "entries", summary["simulated_entries"],
            "closed", summary["closed_count"],
            "open", summary["open_count"],
            "P/L $",
            summary["realized_simulated_pnl_usd"]
        )
