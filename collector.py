
"""GTA6 Learning Collector V6.8 — actieve prioriteit en slimme cooldown.

Behoudt bestaande learning.db, outcome-labels, open papertrades
en JSON-contracten. Geen echte orders.
"""

import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DB = Path(os.getenv("LEARNING_DB", "data/learning.db"))
LATEST = Path(os.getenv("LATEST_JSON", "data/latest_scan.json"))
PAPER_STATE = Path(os.getenv("PAPER_STATE", "data/v591/portfolio.json"))
API = "https://api.dexscreener.com"

SEARCHES = [
    "GTA6", "GTA VI", "Grand Theft Auto", "Vice City",
    "Leonida", "Lucia Camino", "Jason Duval", "Port Gellhorn",
]

KEYWORDS = [
    "gta6", "gta 6", "gta vi", "gtavi", "grand theft auto",
    "grandtheftauto", "vice city", "leonida", "lucia camino",
    "jason duval", "port gellhorn", "raul bautista",
]

ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Alleen scanplanning, geen wijziging aan handelsfilters.
ACTIVE_HOURS = 2
ACTIVE_LIMIT = 80
EARLY_FOLLOW_HOURS = 3
EARLY_FOLLOW_LIMIT = 90
KNOWN_SEARCH_LIMIT = 35
ROTATION_LIMIT = 20
FOLLOW_MIN_LIQ = 5000.0
FOLLOW_MIN_VOL = 50.0
MIN_GAP_MINUTES = 3.5
SEARCH_REVISIT_MINUTES = 12
DORMANT_REVISIT_MINUTES = 60
EARLY_DORMANT_AFTER_MINUTES = 30
EARLY_DORMANT_INTERVAL_MINUTES = 20
ROTATION_CANDIDATE_LIMIT = 200

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GTA6-Learning-Research/6.8",
    "Accept": "application/json",
})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def data_get(path, params=None):
    for attempt in range(3):
        try:
            response = SESSION.get(
                API + path, params=params, timeout=22
            )
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    "HTTP " + str(response.status_code)
                )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def connect():
    # Nooit een ontbrekende database automatisch aanmaken.
    if not DB.is_file():
        raise FileNotFoundError(
            f"Learning DB ontbreekt: {DB}. "
            "Geen nieuwe DB aangemaakt."
        )

    conn = sqlite3.connect(str(DB), timeout=30)

    try:
        conn.execute("PRAGMA busy_timeout=30000")

        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError(
                "Learning DB integriteitscontrole mislukt"
            )

        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        required = {"snapshots", "tokens", "outcomes", "runs"}

        if not required.issubset(existing):
            raise sqlite3.DatabaseError(
                "Learning DB mist tabellen: "
                + ", ".join(sorted(required - existing))
            )

        return conn

    except Exception:
        conn.close()
        raise


def paper_positions():
    """Volg open A/B/C-posities en onthoud hun originele pairs."""

    if not PAPER_STATE.is_file():
        raise FileNotFoundError(
            "Paperportfolio ontbreekt; collector blokkeert "
            "om gegevensverlies te voorkomen"
        )

    state = json.loads(
        PAPER_STATE.read_text(encoding="utf-8")
    )

    if (
        state.get("version") != 1
        or not isinstance(state.get("open"), dict)
    ):
        raise ValueError(
            "Ongeldige paperportfolio; niets gewijzigd"
        )

    pinned = {}

    for strategy in ("A", "B", "C"):
        positions = state["open"].get(strategy)

        if not isinstance(positions, dict):
            raise ValueError(
                "Ongeldige open posities: " + strategy
            )

        for mint, position in positions.items():
            if ADDRESS.fullmatch(mint):
                pair = (
                    position.get("pair")
                    if isinstance(position, dict)
                    else None
                )

                pinned.setdefault(mint, set())

                if isinstance(pair, str) and pair:
                    pinned[mint].add(pair)

    return pinned


def discovery(conn, pinned):
    selected, errors = {}, []

    lookup = {
        row[0]: row[1:]
        for row in conn.execute(
            "SELECT address, first_seen, last_seen FROM tokens"
        )
    }

    fresh_discovered = set()
    known_search = set()
    now_dt = datetime.now(timezone.utc)

    cooldown_skipped = {
        "early": 0,
        "revisit": 0,
        "rotation": 0,
    }

    # Laatste marktmeting per token.
    last_market = {}

    for mint, stamp, vol in conn.execute("""
        SELECT s.token_address, s.scanned_at, s.volume_m5
        FROM snapshots s
        INNER JOIN (
            SELECT token_address, MAX(scanned_at) AS latest
            FROM snapshots
            GROUP BY token_address
        ) x
        ON x.token_address = s.token_address
        AND x.latest = s.scanned_at
        ORDER BY s.token_address
    """):
        last_market.setdefault(
            mint, (stamp, num(vol))
        )

    def elapsed_minutes(stamp):
        try:
            parsed = datetime.fromisoformat(
                str(stamp).replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return max(
                0.0,
                (now_dt - parsed).total_seconds() / 60
            )

        except (TypeError, ValueError, OverflowError):
            return float("inf")

    def cooling_down(mint, duration):
        latest = last_market.get(mint)

        if latest is None:
            return False

        stamp, volume = latest

        return (
            (volume is None or volume <= 0)
            and elapsed_minutes(stamp) < duration
        )

    def add(mint, source):
        if (
            isinstance(mint, str)
            and ADDRESS.fullmatch(mint)
        ):
            selected.setdefault(mint, set()).add(source)

    def candidate(mint, source):
        if (
            not isinstance(mint, str)
            or not ADDRESS.fullmatch(mint)
        ):
            return

        if mint not in lookup:
            fresh_discovered.add(mint)
            add(mint, source)
        else:
            known_search.add(mint)

    # Nieuwe tokens altijd ontdekken.
    for term in SEARCHES:
        try:
            payload = data_get(
                "/latest/dex/search", {"q": term}
            )

            for pair in payload.get("pairs") or []:
                if (
                    not isinstance(pair, dict)
                    or pair.get("chainId") != "solana"
                ):
                    continue

                base = pair.get("baseToken") or {}

                title = (
                    str(base.get("name") or "")
                    + " "
                    + str(base.get("symbol") or "")
                ).lower()

                if any(k in title for k in KEYWORDS):
                    candidate(
                        base.get("address"), "search_new"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            errors.append(
                "search " + term + ": " + str(exc)[:140]
            )

    # Nieuwe profielen en boosts ontdekken.
    for path in (
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ):
        try:
            payload = data_get(path)

            for item in (
                payload if isinstance(payload, list) else []
            ):
                if (
                    not isinstance(item, dict)
                    or item.get("chainId") != "solana"
                ):
                    continue

                links = " ".join(
                    str(x.get("url") or "")
                    for x in item.get("links") or []
                    if isinstance(x, dict)
                )

                context = (
                    str(item.get("description") or "")
                    + " "
                    + links
                ).lower()

                if any(k in context for k in KEYWORDS):
                    candidate(
                        item.get("tokenAddress"),
                        "profile_new"
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            errors.append(
                "profile " + path + ": " + str(exc)[:140]
            )

    # Actieve tokens krijgen voorrang.
    cutoff = (
        now_dt - timedelta(hours=ACTIVE_HOURS)
    ).isoformat(timespec="seconds")

    active = conn.execute("""
        WITH ranked AS (
            SELECT
                token_address,
                scanned_at,
                liquidity_usd,
                volume_m5,
                ROW_NUMBER() OVER (
                    PARTITION BY token_address
                    ORDER BY scanned_at DESC, liquidity_usd DESC
                ) AS rn
            FROM snapshots
            WHERE scanned_at >= ?
        )
        SELECT token_address
        FROM ranked
        WHERE rn = 1
          AND COALESCE(volume_m5, 0) >= ?
          AND COALESCE(liquidity_usd, 0) >= ?
        ORDER BY volume_m5 DESC, scanned_at DESC
        LIMIT ?
    """, (
        cutoff,
        FOLLOW_MIN_VOL,
        FOLLOW_MIN_LIQ,
        ACTIVE_LIMIT,
    )).fetchall()

    for (mint,) in active:
        add(mint, "active_follow")

    # Nieuwe tokens tot 3 uur opvolgen.
    early_cutoff = (
        now_dt - timedelta(hours=EARLY_FOLLOW_HOURS)
    ).isoformat(timespec="seconds")

    early = conn.execute("""
        WITH ranked AS (
            SELECT
                token_address,
                scanned_at,
                pair_age_hours,
                volume_m5,
                ROW_NUMBER() OVER (
                    PARTITION BY token_address
                    ORDER BY scanned_at DESC
                ) AS rn
            FROM snapshots
            WHERE scanned_at >= ?
        )
        SELECT r.token_address
        FROM ranked r
        JOIN tokens t
          ON t.address = r.token_address
        WHERE r.rn = 1
          AND t.first_seen >= ?
          AND (
              r.pair_age_hours BETWEEN 0 AND 24
              OR r.pair_age_hours IS NULL
          )
        ORDER BY
            COALESCE(r.volume_m5, 0) DESC,
            r.scanned_at DESC
        LIMIT ?
    """, (
        early_cutoff,
        early_cutoff,
        EARLY_FOLLOW_LIMIT,
    )).fetchall()

    for (mint,) in early:
        first = lookup.get(mint, (None, None))[0]
        first_age = elapsed_minutes(first)

        if (
            first_age > EARLY_DORMANT_AFTER_MINUTES
            and cooling_down(
                mint, EARLY_DORMANT_INTERVAL_MINUTES
            )
            and mint not in pinned
        ):
            cooldown_skipped["early"] += 1
            continue

        add(mint, "early_follow")

    # Bekende zoekresultaten periodiek hercontroleren.
    revisit_cutoff = (
        now_dt - timedelta(
            minutes=SEARCH_REVISIT_MINUTES
        )
    )

    revisit = []

    for mint in known_search:
        last_seen = lookup[mint][1]

        try:
            last_dt = datetime.fromisoformat(
                last_seen.replace("Z", "+00:00")
            )

            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(
                    tzinfo=timezone.utc
                )

        except (TypeError, ValueError):
            continue

        if last_dt <= revisit_cutoff:
            revisit.append((last_dt, mint))

    revisit.sort()
    actual_revisit = 0

    for _, mint in revisit:
        if actual_revisit >= KNOWN_SEARCH_LIMIT:
            break

        if (
            mint not in pinned
            and cooling_down(
                mint, DORMANT_REVISIT_MINUTES
            )
        ):
            cooldown_skipped["revisit"] += 1
            continue

        add(mint, "search_revisit")
        actual_revisit += 1

    # Rotatie van oudere tokens.
    rotation = conn.execute("""
        SELECT address
        FROM tokens
        ORDER BY last_seen ASC
        LIMIT ?
    """, (
        ROTATION_CANDIDATE_LIMIT,
    )).fetchall()

    actual_rotation = 0

    for (mint,) in rotation:
        if actual_rotation >= ROTATION_LIMIT:
            break

        if (
            mint not in pinned
            and cooling_down(
                mint, DORMANT_REVISIT_MINUTES
            )
        ):
            cooldown_skipped["rotation"] += 1
            continue

        add(mint, "history_rotation")
        actual_rotation += 1

    # Open paperposities altijd blijven volgen.
    for mint in pinned:
        add(mint, "paper_open")

    summary = {
        "new_search_tokens": len(fresh_discovered),
        "active_follow": len(active),
        "early_follow": sum(
            "early_follow" in src
            for src in selected.values()
        ),
        "search_revisit": actual_revisit,
        "rotation": actual_rotation,
        "cooldown_skipped": cooldown_skipped,
        "pinned_open": len(pinned),
        "selected_total": len(selected),
    }

    print(json.dumps(
        summary, ensure_ascii=False
    ))

    return selected, errors, summary


def fetch_pairs(addresses, pinned):
    found, errors = {}, []

    for start in range(0, len(addresses), 30):
        group = addresses[start:start + 30]

        try:
            payload = data_get(
                "/tokens/v1/solana/" + ",".join(group)
            )

            if not isinstance(payload, list):
                raise ValueError(
                    "Unexpected response type"
                )

            for pair in payload:
                if (
                    not isinstance(pair, dict)
                    or pair.get("chainId") != "solana"
                ):
                    continue

                base = pair.get("baseToken") or {}

                mint = base.get("address")
                pair_id = pair.get("pairAddress")

                if (
                    mint not in group
                    or not isinstance(pair_id, str)
                    or not pair_id
                ):
                    continue

                old = found.get(mint)
                pinned_pairs = pinned.get(mint, set())

                matching = pair_id in pinned_pairs
                old_matching = (
                    old is not None
                    and old.get("pairAddress") in pinned_pairs
                )

                liquidity = (
                    num(
                        (pair.get("liquidity") or {})
                        .get("usd")
                    ) or 0
                )

                old_liq = (
                    num(
                        (old.get("liquidity") or {})
                        .get("usd")
                    ) or 0
                ) if old else -1

                if (
                    old is None
                    or (matching and not old_matching)
                    or (
                        matching == old_matching
                        and liquidity > old_liq
                    )
                ):
                    found[mint] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            errors.append(
                f"batch {start // 30 + 1}: "
                + str(exc)[:140]
            )

    return found, errors


def save_snapshots(conn, pairs, sources, stamp):
    recorded = 0
    skipped_short = 0
    skipped_missing_price = 0

    current = datetime.fromisoformat(
        stamp.replace("Z", "+00:00")
    )

    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timezone.utc
        )

    current_ms = current.timestamp() * 1000

    for mint, pair in pairs.items():
        pair_id = pair["pairAddress"]

        prior = conn.execute("""
            SELECT scanned_at
            FROM snapshots
            WHERE token_address = ?
              AND pair_address = ?
            ORDER BY scanned_at DESC
            LIMIT 1
        """, (mint, pair_id)).fetchone()

        if prior:
            try:
                past = datetime.fromisoformat(
                    prior[0].replace("Z", "+00:00")
                )

                if past.tzinfo is None:
                    past = past.replace(
                        tzinfo=timezone.utc
                    )

                gap = (
                    current - past
                ).total_seconds() / 60

                if gap < MIN_GAP_MINUTES:
                    skipped_short += 1
                    continue

            except (ValueError, TypeError):
                pass

        base = pair.get("baseToken") or {}

        name = str(
            base.get("name") or ""
        )[:200]

        symbol = str(
            base.get("symbol") or ""
        )[:60]

        price = num(pair.get("priceUsd"))

        liq = num(
            (pair.get("liquidity") or {}).get("usd")
        )

        volume = num(
            (pair.get("volume") or {}).get("m5")
        )

        tx = (
            (pair.get("txns") or {}).get("m5")
            or {}
        )

        buys = int(num(tx.get("buys")) or 0)
        sells = int(num(tx.get("sells")) or 0)

        created = num(pair.get("pairCreatedAt"))

        age = (
            max(
                0,
                (current_ms - created) / 3600000
            )
            if created else None
        )

        avg = (
            volume / (buys + sells)
            if volume is not None
            and buys + sells > 0
            else None
        )

        conn.execute("""
            INSERT INTO tokens(
                address,
                name,
                symbol,
                first_seen,
                last_seen
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                name = excluded.name,
                symbol = excluded.symbol,
                last_seen = excluded.last_seen
        """, (
            mint, name, symbol, stamp, stamp
        ))

        cursor = conn.execute("""
            INSERT OR IGNORE INTO snapshots(
                token_address,
                pair_address,
                scanned_at,
                name,
                symbol,
                price_usd,
                liquidity_usd,
                volume_m5,
                buys_m5,
                sells_m5,
                pair_age_hours,
                average_trade_usd,
                source
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            mint,
            pair_id,
            stamp,
            name,
            symbol,
            price,
            liq,
            volume,
            buys,
            sells,
            age,
            avg,
            ",".join(sorted(
                sources.get(mint, [])
            )),
        ))

        recorded += cursor.rowcount

        if price is None or price <= 0:
            skipped_missing_price += 1

    return (
        recorded,
        skipped_short,
        skipped_missing_price,
    )


def build_outcomes(conn):
    inserted = 0

    for horizon in (15, 30, 60):
        baselines = conn.execute("""
            SELECT
                s.token_address,
                s.pair_address,
                s.scanned_at,
                s.price_usd,
                s.liquidity_usd
            FROM snapshots s
            WHERE s.price_usd > 0
              AND julianday('now')
                - julianday(s.scanned_at)
                >= ? / 1440.0
              AND NOT EXISTS (
                  SELECT 1
                  FROM outcomes o
                  WHERE o.token_address = s.token_address
                    AND o.pair_address = s.pair_address
                    AND o.baseline_at = s.scanned_at
                    AND o.horizon_minutes = ?
              )
            ORDER BY s.scanned_at DESC
            LIMIT 2000
        """, (horizon, horizon)).fetchall()

        for (
            mint,
            pair,
            baseline,
            price,
            liquidity,
        ) in baselines:

            future = conn.execute("""
                SELECT
                    scanned_at,
                    price_usd,
                    liquidity_usd
                FROM snapshots
                WHERE token_address = ?
                  AND pair_address = ?
                  AND price_usd > 0
                  AND julianday(scanned_at)
                      >= julianday(?) + ? / 1440.0
                  AND julianday(scanned_at)
                      <= julianday(?) + (? + 20) / 1440.0
                ORDER BY scanned_at ASC
                LIMIT 1
            """, (
                mint,
                pair,
                baseline,
                horizon,
                baseline,
                horizon,
            )).fetchone()

            if future is None:
                continue

            (
                observed,
                future_price,
                future_liq,
            ) = future

            change = 100 * (
                future_price / price - 1
            )

            liq_change = (
                100 * (
                    future_liq / liquidity - 1
                )
                if liquidity
                and future_liq is not None
                else None
            )

            cursor = conn.execute("""
                INSERT OR IGNORE INTO outcomes(
                    token_address,
                    pair_address,
                    baseline_at,
                    horizon_minutes,
                    observed_at,
                    baseline_price,
                    future_price,
                    return_pct,
                    baseline_liquidity,
                    future_liquidity,
                    liquidity_change_pct,
                    crash_30pct,
                    liquidity_drop_50pct
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                mint,
                pair,
                baseline,
                horizon,
                observed,
                price,
                future_price,
                change,
                liquidity,
                future_liq,
                liq_change,
                int(change <= -30),
                (
                    int(liq_change <= -50)
                    if liq_change is not None
                    else None
                ),
            ))

            inserted += cursor.rowcount

    return inserted


def run():
    started = now()

    # Lees en bescherm open paperposities.
    pinned = paper_positions()

    with connect() as conn:
        addresses, errors, selection = discovery(
            conn, pinned
        )

        # Open posities en actieve tokens krijgen prioriteit.
        def priority(mint):
            src = addresses[mint]

            return (
                0 if "paper_open" in src else
                1 if (
                    "search_new" in src
                    or "profile_new" in src
                ) else
                2 if "active_follow" in src else
                3 if "early_follow" in src else
                4 if "search_revisit" in src else
                5,
                mint,
            )

        tokens = sorted(
            addresses, key=priority
        )

        pairs, more_errors = fetch_pairs(
            tokens, pinned
        )

        errors.extend(more_errors)

        recorded, skipped, bad_price = save_snapshots(
            conn,
            pairs,
            addresses,
            started,
        )

        new_labels = build_outcomes(conn)
        finished = now()

        conn.execute("""
            INSERT OR REPLACE INTO runs(
                started_at,
                finished_at,
                discovered,
                snapshots,
                error_count,
                errors
            )
            VALUES(?,?,?,?,?,?)
        """, (
            started,
            finished,
            len(addresses),
            recorded,
            len(errors),
            json.dumps(errors),
        ))

        conn.commit()

        total_labels = conn.execute(
            "SELECT COUNT(*) FROM outcomes"
        ).fetchone()[0]

        total_snapshots = conn.execute(
            "SELECT COUNT(*) FROM snapshots"
        ).fetchone()[0]

    summary = {
        "started_at_utc": started,
        "finished_at_utc": finished,
        "discovered": len(addresses),
        "recorded": recorded,
        "skipped_short_interval": skipped,
        "open_paper_tokens_followed": len(pinned),
        "new_labels": new_labels,
        "total_labels": total_labels,
        "total_snapshots": total_snapshots,
        "errors": errors[:25],
        "selection": selection,
        "pairs_with_market_data": len(pairs),
        "invalid_price_snapshots": bad_price,
        "note": (
            "Research only: een marktmeting bevestigt "
            "geen tokenveiligheid of verkoopbaarheid."
        ),
    }

    LATEST.parent.mkdir(
        parents=True, exist_ok=True
    )

    LATEST.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(
        summary,
        ensure_ascii=False,
    ))

    if recorded == 0 and errors:
        raise RuntimeError(
            "No snapshots recorded; API errors detected"
        )


if __name__ == "__main__":
    run()
