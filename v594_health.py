
"""V5.9.4 - scanhistoriek onafhankelijk van handelsdatabases."""

import json
from datetime import datetime, timezone
from pathlib import Path

LATEST = Path("data/latest_scan.json")
PAPER = Path("data/v591/preview.json")
HISTORY = Path("data/v594/scan_history.json")

MAX_RECORDS = 120


def read(path):
    obj = json.loads(
        path.read_text(encoding="utf-8")
    )

    if not isinstance(obj, dict):
        raise ValueError(
            f"Ongeldig JSON-object: {path}"
        )

    return obj


def main():
    latest = read(LATEST)
    paper = read(PAPER)

    if paper.get("mode") != "ISOLATED_PAPER_STATE":
        raise ValueError(
            "Paper-engine rapport heeft verkeerde modus"
        )

    stamp = latest.get("finished_at_utc")

    if not stamp or not latest.get("started_at_utc"):
        raise ValueError(
            "Collector timestamps ontbreken"
        )

    datetime.fromisoformat(
        stamp.replace("Z", "+00:00")
    )

    errors = latest.get("errors") or []

    if not isinstance(errors, list):
        raise ValueError(
            "Collector errors moeten een lijst zijn"
        )

    recorded = int(latest.get("recorded") or 0)

    current = {
        "started_at_utc": latest["started_at_utc"],
        "finished_at_utc": stamp,
        "recorded": recorded,
        "discovered": int(
            latest.get("discovered") or 0
        ),
        "new_labels": int(
            latest.get("new_labels") or 0
        ),
        "signals": int(
            (paper.get("run") or {}).get("signals") or 0
        ),
        "paper_observations": int(
            (paper.get("run") or {}).get("observations") or 0
        ),
        "paper_open_a": int(
            ((paper.get("portfolio") or {}).get("A") or {})
            .get("open") or 0
        ),
        "paper_open_b": int(
            ((paper.get("portfolio") or {}).get("B") or {})
            .get("open") or 0
        ),
        "error_count": len(errors),
        "errors": [
            str(e)[:240] for e in errors[:5]
        ],
        "status": (
            "WAARSCHUWING"
            if errors or recorded == 0
            else "OK"
        )
    }

    if HISTORY.exists():
        history = read(HISTORY)

        if (
            history.get("schema_version") != 1
            or not isinstance(history.get("runs"), list)
        ):
            raise ValueError(
                "Onbekend historiekformaat"
            )

        records = history["runs"]
    else:
        records = []

    # Voorkom dubbele records bij opnieuw uitvoeren.
    records = [
        r for r in records
        if r.get("started_at_utc")
        != current["started_at_utc"]
    ]

    records.append(current)

    records = sorted(
        records,
        key=lambda r: r["started_at_utc"]
    )[-MAX_RECORDS:]

    output = {
        "schema_version": 1,
        "updated_at_utc": (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
        ),
        "runs": records
    }

    HISTORY.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = HISTORY.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    temporary.replace(HISTORY)

    print(
        f"V5.9.4: {current['status']} | "
        f"historiek: {len(records)} runs"
    )


if __name__ == "__main__":
    main()
