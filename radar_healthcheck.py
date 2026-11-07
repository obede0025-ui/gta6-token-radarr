
"""GTA6 Radar Healthcheck V1 — uitsluitend lezen."""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def utc(value):
    if not value:
        return None

    dt = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    return (
        dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None
        else dt.astimezone(timezone.utc)
    )


def read_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def healthcheck(root):
    root = Path(root)

    latest = read_json(
        root / "data/latest_scan.json"
    )
    preview = read_json(
        root / "data/v591/preview.json"
    )
    state = read_json(
        root / "data/v591/portfolio.json"
    )

    checked = datetime.now(timezone.utc)
    end = utc(latest.get("finished_at_utc"))

    age_min = (
        round((checked - end).total_seconds() / 60, 1)
        if end else None
    )

    alerts = []

    if age_min is None or age_min > 15:
        alerts.append(
            "SCAN_VERTRAAGD: laatste scan ouder dan 15 minuten"
        )

    if latest.get("errors"):
        alerts.append(
            "COLLECTOR_FOUTEN: controleer latest_scan.json"
        )

    if preview.get("mode") != "ISOLATED_PAPER_STATE":
        alerts.append("PAPER_MODUS_ONVERWACHT")

    if state.get("version") != 1:
        raise ValueError(
            "Onverwachte portfolio-versie"
        )

    positions = []
    concentrations = defaultdict(list)

    for strategy in ("A", "B", "C"):
        opened = (
            state.get("open", {}).get(strategy) or {}
        )

        for mint, position in opened.items():
            concentrations[mint].append(strategy)

            positions.append({
                "strategy": strategy,
                "token": mint,
                "name": position.get("name", ""),
                "pair": position.get("pair"),
                "opened_at": position.get("opened_at"),
                "age_hours": round(
                    (
                        checked
                        - utc(position["opened_at"])
                    ).total_seconds() / 3600,
                    2,
                ),
                "entry_market": position.get(
                    "entry_market"
                ),
                "last_price": None,
                "last_market_age_minutes": None,
                "gross_price_change_pct": None,
                "market_liquidity_usd": None,
            })

    for mint, strategies in concentrations.items():
        if len(strategies) > 1:
            alerts.append(
                "GECORRELEERD_MARKTRISICO: "
                + ",".join(strategies)
                + " volgen dezelfde token "
                + mint
            )

    database = root / "data/learning.db"

    if not database.is_file():
        alerts.append(
            "LEARNING_DB_ONTBREEKT: prijscontrole overgeslagen"
        )

    else:
        uri = database.resolve().as_uri() + "?mode=ro"

        with sqlite3.connect(uri, uri=True) as conn:
            check = conn.execute(
                "PRAGMA quick_check"
            ).fetchone()

            if not check or check[0] != "ok":
                raise RuntimeError(
                    "Learning database niet integer"
                )

            for item in positions:
                row = conn.execute("""
                    SELECT
                        scanned_at,
                        price_usd,
                        liquidity_usd
                    FROM snapshots
                    WHERE token_address = ?
                      AND pair_address = ?
                    ORDER BY scanned_at DESC
                    LIMIT 1
                """, (
                    item["token"],
                    item["pair"],
                )).fetchone()

                if not row:
                    alerts.append(
                        "GEEN_MARKTMETING: "
                        + item["strategy"]
                        + "/"
                        + item["token"]
                    )
                    continue

                stamp, price, liquidity = row

                lag = (
                    checked - utc(stamp)
                ).total_seconds() / 60

                item["last_market_age_minutes"] = round(
                    lag, 1
                )

                item["last_price"] = price
                item["market_liquidity_usd"] = liquidity

                if (
                    price is not None
                    and item["entry_market"]
                ):
                    item["gross_price_change_pct"] = round(
                        100 * (
                            price / item["entry_market"] - 1
                        ),
                        2,
                    )

                if lag > 15:
                    alerts.append(
                        "VEROUDERDE_POSITIEMETING: "
                        + item["strategy"]
                        + "/"
                        + item["token"]
                    )

                if (
                    liquidity is None
                    or liquidity < 10000
                ):
                    alerts.append(
                        "LAGE_LIQUIDITEIT: "
                        + item["strategy"]
                        + "/"
                        + item["token"]
                    )

    closed = {}

    for strategy in ("A", "B", "C"):
        trades = (
            state.get("closed", {}).get(strategy) or []
        )

        closed[strategy] = {
            "count": len(trades),
            "realized_pnl_usd": round(
                sum(
                    float(t.get("pnl_usd") or 0)
                    for t in trades
                ),
                4,
            ),
        }

    return {
        "checked_at_utc": checked.isoformat(
            timespec="seconds"
        ),
        "last_scan_utc": latest.get(
            "finished_at_utc"
        ),
        "scan_age_minutes": age_min,
        "selected": latest.get("discovered"),
        "recorded": latest.get("recorded"),
        "selection": latest.get("selection", {}),
        "signals_last_run": preview.get(
            "run", {}
        ).get("signals", 0),
        "open_positions": positions,
        "closed_portfolios": closed,
        "alerts": alerts,
        "status": (
            "AANDACHT" if alerts else "OK"
        ),
        "disclaimer": (
            "Uitsluitend papertrading. "
            "Marktprijzen zijn geen uitvoerbare verkoopquotes."
        ),
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        default=".",
    )

    args = parser.parse_args()

    print(
        json.dumps(
            healthcheck(args.root),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
