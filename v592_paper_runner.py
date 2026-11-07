
"""V5.9.2 - veilige automatische achtergrondportefeuille.

Gebruikt de bestaande V5.9.1-engine.
Wijzigt geen Streamlit- of Supabase-papertrades.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from v591_paper_engine import load_state


DB = Path("data/learning.db")
STATE = Path("data/v591/portfolio.json")
REPORT = Path("data/v591/preview.json")


def bootstrap_if_needed():
    if STATE.exists():
        state = load_state(STATE)

        if not isinstance(
            state.get("cursor"),
            (list, type(None))
        ):
            raise ValueError(
                "Portfolio-cursor ongeldig"
            )

        print(
            "Bestaande V5.9.2-portefeuille behouden"
        )
        return

    if not DB.is_file():
        raise FileNotFoundError(
            "Learning Collector database ontbreekt"
        )

    uri = DB.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(
        uri,
        uri=True
    ) as conn:

        result = conn.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]

        if result != "ok":
            raise RuntimeError(
                "Collector-database niet integer"
            )

        latest = conn.execute(
            """
            SELECT scanned_at,
                   token_address,
                   pair_address
            FROM snapshots
            ORDER BY scanned_at DESC,
                     token_address DESC,
                     pair_address DESC
            LIMIT 1
            """
        ).fetchone()

    if latest is None:
        raise RuntimeError(
            "Geen marktmetingen beschikbaar"
        )

    state = load_state(STATE)

    # Start NA de laatste historische meting.
    # Daardoor openen we geen oude trades achteraf.
    state["cursor"] = list(latest)

    STATE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Exclusief nieuw bestand: geen bestaande state overschrijven.
    with STATE.open(
        "x",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            indent=2,
            ensure_ascii=False
        )

        file.write("\n")

    print(
        "Nieuwe achtergrondportefeuille gestart na:",
        latest[0]
    )

    print(
        "Geen retrospectieve paperinstappen"
    )


def main():
    bootstrap_if_needed()

    command = [
        sys.executable,
        "v591_paper_engine.py",
        "--save-state",
        "--db",
        str(DB),
        "--state",
        str(STATE),
        "--output",
        str(REPORT),
    ]

    subprocess.run(
        command,
        check=True
    )

    report = json.loads(
        REPORT.read_text(
            encoding="utf-8"
        )
    )

    if report.get("mode") != "ISOLATED_PAPER_STATE":
        raise RuntimeError(
            "Onverwachte engine-modus"
        )

    print(
        "V5.9.2 portefeuille opgeslagen"
    )

    print(
        "Laatste verwerkte meting:",
        report["last_processed"]
    )


if __name__ == "__main__":
    main()
