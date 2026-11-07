
"""GTA6 Learning Collector V6.6.

Nieuwe tokens + actieve vervolgmetingen + roterende controle.
Bestaande SQLite-tabellen, paperportfolio en leerlabels blijven intact.
Geen automatische walletclaims: walletdata staat buiten deze collector.
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

DB = Path(os.getenv('LEARNING_DB', 'data/learning.db'))
LATEST = Path(os.getenv('LATEST_JSON', 'data/latest_scan.json'))
PAPER_STATE = Path(os.getenv('PAPER_STATE', 'data/v591/portfolio.json'))
API = 'https://api.dexscreener.com'

SEARCHES = [
    'GTA6', 'GTA VI', 'Grand Theft Auto', 'Vice City',
    'Leonida', 'Lucia Camino', 'Jason Duval', 'Port Gellhorn',
]
KEYWORDS = [
    'gta6', 'gta 6', 'gta vi', 'gtavi', 'grand theft auto',
    'grandtheftauto', 'vice city', 'leonida', 'lucia camino',
    'jason duval', 'port gellhorn', 'raul bautista',
]
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

ACTIVE_HOURS = 2
ACTIVE_LIMIT = 100
ROTATION_LIMIT = 35
FOLLOW_MIN_LIQ = 5000.0
FOLLOW_MIN_VOL = 50.0
MIN_GAP_MINUTES = 3.5

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'GTA6-Learning-Research/6.6',
    'Accept': 'application/json',
})


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def num(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
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
                    f'HTTP {response.status_code}'
                )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB), timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=DELETE')

    conn.execute('''CREATE TABLE IF NOT EXISTS tokens (
        address TEXT PRIMARY KEY,
        name TEXT,
        symbol TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        token_address TEXT NOT NULL,
        pair_address TEXT NOT NULL,
        scanned_at TEXT NOT NULL,
        name TEXT,
        symbol TEXT,
        price_usd REAL,
        liquidity_usd REAL,
        volume_m5 REAL,
        buys_m5 INTEGER,
        sells_m5 INTEGER,
        pair_age_hours REAL,
        average_trade_usd REAL,
        source TEXT,
        PRIMARY KEY(token_address,pair_address,scanned_at)
    )''')

    conn.execute('''CREATE INDEX IF NOT EXISTS snapshots_lookup
        ON snapshots(token_address,pair_address,scanned_at)
    ''')

    conn.execute('''CREATE TABLE IF NOT EXISTS outcomes (
        token_address TEXT NOT NULL,
        pair_address TEXT NOT NULL,
        baseline_at TEXT NOT NULL,
        horizon_minutes INTEGER NOT NULL,
        observed_at TEXT NOT NULL,
        baseline_price REAL NOT NULL,
        future_price REAL NOT NULL,
        return_pct REAL NOT NULL,
        baseline_liquidity REAL,
        future_liquidity REAL,
        liquidity_change_pct REAL,
        crash_30pct INTEGER NOT NULL,
        liquidity_drop_50pct INTEGER,
        PRIMARY KEY(
            token_address,pair_address,
            baseline_at,horizon_minutes
        )
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS runs (
        started_at TEXT PRIMARY KEY,
        finished_at TEXT,
        discovered INTEGER,
        snapshots INTEGER,
        error_count INTEGER,
        errors TEXT
    )''')

    conn.commit()
    return conn


def open_paper_addresses():
    """Volg bestaande A/B/C-posities, ook zonder volume."""
    if not PAPER_STATE.exists():
        return set()

    state = json.loads(
        PAPER_STATE.read_text(encoding='utf-8')
    )

    if (
        state.get('version') != 1
        or not isinstance(state.get('open'), dict)
    ):
        raise ValueError(
            'Onbekende paperportfolio; collector gestopt'
        )

    result = set()

    for strategy in ('A', 'B', 'C'):
        positions = state['open'].get(strategy) or {}

        if not isinstance(positions, dict):
            raise ValueError(
                f'Ongeldige open paperposities {strategy}'
            )

        result.update(
            a for a in positions
            if ADDRESS.fullmatch(a)
        )

    return result


def discovery(conn, pinned):
    addresses = {}
    errors = []

    previously_seen = {
        r[0] for r in conn.execute(
            'SELECT address FROM tokens'
        )
    }
    raw_search = set()

    def add(address, source):
        if (
            isinstance(address, str)
            and ADDRESS.fullmatch(address)
        ):
            addresses.setdefault(
                address, set()
            ).add(source)

    def search_candidate(address, source):
        if (
            isinstance(address, str)
            and ADDRESS.fullmatch(address)
        ):
            raw_search.add(address)

            # Nieuwe zoekresultaten direct scannen.
            # Bekende adressen volgen via actief/rotatie.
            if address not in previously_seen:
                add(address, source)

    # Nieuwe GTA6-gerelateerde tokens ontdekken.
    for term in SEARCHES:
        try:
            payload = data_get(
                '/latest/dex/search', {'q': term}
            )

            for pair in payload.get('pairs') or []:
                if (
                    not isinstance(pair, dict)
                    or pair.get('chainId') != 'solana'
                ):
                    continue

                base = pair.get('baseToken') or {}
                title = (
                    f"{base.get('name', '')} "
                    f"{base.get('symbol', '')}"
                ).lower()

                if any(k in title for k in KEYWORDS):
                    search_candidate(
                        base.get('address'),
                        'search_new'
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError,
            AttributeError
        ) as exc:
            errors.append(
                f'search {term}: {str(exc)[:140]}'
            )

    # Ook nieuwe profielen en boosts controleren.
    for path in (
        '/token-profiles/latest/v1',
        '/token-boosts/latest/v1',
        '/token-boosts/top/v1',
    ):
        try:
            payload = data_get(path)

            for item in (
                payload if isinstance(payload, list) else []
            ):
                if (
                    not isinstance(item, dict)
                    or item.get('chainId') != 'solana'
                ):
                    continue

                links = ' '.join(
                    str(x.get('url') or '')
                    for x in item.get('links') or []
                    if isinstance(x, dict)
                )

                context = (
                    f"{item.get('description') or ''} "
                    f"{links}"
                ).lower()

                if any(k in context for k in KEYWORDS):
                    search_candidate(
                        item.get('tokenAddress'),
                        'profile_new'
                    )

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f'profile {path}: {str(exc)[:140]}'
            )

    # Bestaande tokens met recente activiteit.
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(hours=ACTIVE_HOURS)
    ).isoformat(timespec='seconds')

    active = conn.execute('''
        WITH ranked AS (
            SELECT
                token_address,
                scanned_at,
                liquidity_usd,
                volume_m5,
                buys_m5,
                sells_m5,
                pair_age_hours,
                ROW_NUMBER() OVER (
                    PARTITION BY token_address
                    ORDER BY scanned_at DESC,
                             liquidity_usd DESC
                ) AS position
            FROM snapshots
            WHERE scanned_at >= ?
        )
        SELECT token_address
        FROM ranked
        WHERE position = 1
          AND COALESCE(volume_m5,0) >= ?
          AND COALESCE(liquidity_usd,0) >= ?
          AND COALESCE(buys_m5,0)
              > COALESCE(sells_m5,0)
          AND pair_age_hours BETWEEN 0 AND 24
        ORDER BY
          CASE
            WHEN volume_m5 >= 250
             AND liquidity_usd >= 10000
            THEN 1 ELSE 0
          END DESC,
          volume_m5 DESC,
          scanned_at DESC
        LIMIT ?
    ''', (
        cutoff,
        FOLLOW_MIN_VOL,
        FOLLOW_MIN_LIQ,
        ACTIVE_LIMIT
    )).fetchall()

    for (address,) in active:
        add(address, 'active_follow')

    # Oudere tokens roterend blijven controleren.
    rotation = conn.execute('''
        SELECT address FROM tokens
        ORDER BY last_seen ASC
        LIMIT ?
    ''', (ROTATION_LIMIT,)).fetchall()

    for (address,) in rotation:
        add(address, 'history_rotation')

    # Open paperposities krijgen altijd prioriteit.
    for address in pinned:
        add(address, 'paper_open')

    print(json.dumps({
        'new_search_tokens': len(
            raw_search - previously_seen
        ),
        'active_follow': len(active),
        'rotation': len(rotation),
        'pinned_open': len(pinned),
        'selected_total': len(addresses),
    }))

    return addresses, errors


def fetch_pairs(addresses):
    found = {}
    errors = []

    for start in range(0, len(addresses), 30):
        group = addresses[start:start + 30]

        try:
            payload = data_get(
                '/tokens/v1/solana/' + ','.join(group)
            )

            if not isinstance(payload, list):
                raise ValueError(
                    'Unexpected response type'
                )

            for pair in payload:
                if (
                    not isinstance(pair, dict)
                    or pair.get('chainId') != 'solana'
                ):
                    continue

                base = pair.get('baseToken') or {}
                address = base.get('address')
                pair_address = pair.get('pairAddress')

                if (
                    address not in group
                    or not isinstance(pair_address, str)
                    or not pair_address
                ):
                    continue

                previous = found.get(address)

                liquidity = num(
                    (pair.get('liquidity') or {}).get('usd')
                ) or 0

                prev_liquidity = (
                    num(
                        (previous.get('liquidity') or {})
                        .get('usd')
                    ) or 0
                ) if previous else -1

                if liquidity > prev_liquidity:
                    found[address] = pair

        except (
            requests.RequestException,
            ValueError,
            TypeError
        ) as exc:
            errors.append(
                f'batch {start // 30 + 1}: '
                f'{str(exc)[:140]}'
            )

    return found, errors


def save_snapshots(
    conn, pairs, sources, stamp, pinned
):
    recorded = 0
    skipped_short = 0

    current = datetime.fromisoformat(stamp)
    current_ms = current.timestamp() * 1000

    for address, pair in pairs.items():
        pair_address = pair['pairAddress']

        # Voorkom onnodig korte intervallen.
        # Open posities blijven altijd opgevolgd.
        if address not in pinned:
            previous = conn.execute('''
                SELECT scanned_at
                FROM snapshots
                WHERE token_address=?
                  AND pair_address=?
                ORDER BY scanned_at DESC
                LIMIT 1
            ''', (
                address, pair_address
            )).fetchone()

            if previous:
                try:
                    delta = (
                        current
                        - datetime.fromisoformat(
                            previous[0].replace(
                                'Z', '+00:00'
                            )
                        )
                    ).total_seconds() / 60

                except (ValueError, TypeError):
                    delta = MIN_GAP_MINUTES

                if delta < MIN_GAP_MINUTES:
                    skipped_short += 1
                    continue

        base = pair.get('baseToken') or {}
        name = str(base.get('name') or '')[:200]
        symbol = str(base.get('symbol') or '')[:60]

        liquidity = num(
            (pair.get('liquidity') or {}).get('usd')
        )
        volume = num(
            (pair.get('volume') or {}).get('m5')
        )

        tx = (
            (pair.get('txns') or {}).get('m5')
            or {}
        )
        buys = int(num(tx.get('buys')) or 0)
        sells = int(num(tx.get('sells')) or 0)

        created = num(pair.get('pairCreatedAt'))
        age = (
            max(
                0,
                (current_ms - created) / 3600000
            )
            if created else None
        )

        average = (
            volume / (buys + sells)
            if volume is not None
            and buys + sells > 0
            else None
        )

        conn.execute('''
            INSERT INTO tokens(
                address, name, symbol,
                first_seen, last_seen
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                name=excluded.name,
                symbol=excluded.symbol,
                last_seen=excluded.last_seen
        ''', (
            address, name, symbol,
            stamp, stamp
        ))

        cursor = conn.execute('''
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
        ''', (
            address,
            pair_address,
            stamp,
            name,
            symbol,
            num(pair.get('priceUsd')),
            liquidity,
            volume,
            buys,
            sells,
            age,
            average,
            ','.join(
                sorted(sources.get(address, []))
            ),
        ))

        recorded += cursor.rowcount

    return recorded, skipped_short


def build_outcomes(conn):
    """Behoud bestaande 15-, 30- en 60-minutenlabels."""
    inserted = 0

    for horizon in (15, 30, 60):
        baselines = conn.execute('''
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
                  WHERE o.token_address =
                        s.token_address
                    AND o.pair_address =
                        s.pair_address
                    AND o.baseline_at =
                        s.scanned_at
                    AND o.horizon_minutes = ?
              )
            ORDER BY s.scanned_at DESC
            LIMIT 2000
        ''', (
            horizon, horizon
        )).fetchall()

        for (
            address, pair, baseline_at,
            price, liquidity
        ) in baselines:

            future = conn.execute('''
                SELECT
                    scanned_at,
                    price_usd,
                    liquidity_usd
                FROM snapshots
                WHERE token_address=?
                  AND pair_address=?
                  AND price_usd > 0
                  AND julianday(scanned_at)
                      >= julianday(?)
                         + ? / 1440.0
                  AND julianday(scanned_at)
                      <= julianday(?)
                         + (? + 20) / 1440.0
                ORDER BY scanned_at ASC
                LIMIT 1
            ''', (
                address,
                pair,
                baseline_at,
                horizon,
                baseline_at,
                horizon
            )).fetchone()

            if future is None:
                continue

            (
                observed_at,
                future_price,
                future_liquidity
            ) = future

            return_pct = 100 * (
                future_price / price - 1
            )

            liquidity_change = (
                100 * (
                    future_liquidity / liquidity - 1
                )
                if liquidity
                and future_liquidity is not None
                else None
            )

            cursor = conn.execute('''
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
            ''', (
                address,
                pair,
                baseline_at,
                horizon,
                observed_at,
                price,
                future_price,
                return_pct,
                liquidity,
                future_liquidity,
                liquidity_change,
                int(return_pct <= -30),
                (
                    int(liquidity_change <= -50)
                    if liquidity_change is not None
                    else None
                ),
            ))

            inserted += cursor.rowcount

    return inserted


def run():
    started = now()

    # Lees open posities voordat discovery begint.
    pinned = open_paper_addresses()

    with connect() as conn:
        addresses, errors = discovery(
            conn, pinned
        )

        pairs, fetch_errors = fetch_pairs(
            list(addresses)
        )
        errors.extend(fetch_errors)

        recorded, skipped = save_snapshots(
            conn,
            pairs,
            addresses,
            started,
            pinned
        )

        new_labels = build_outcomes(conn)
        finished = now()

        conn.execute('''
            INSERT OR REPLACE INTO runs
            VALUES(?,?,?,?,?,?)
        ''', (
            started,
            finished,
            len(addresses),
            recorded,
            len(errors),
            json.dumps(errors),
        ))
        conn.commit()

        total_labels = conn.execute(
            'SELECT COUNT(*) FROM outcomes'
        ).fetchone()[0]

        total_snapshots = conn.execute(
            'SELECT COUNT(*) FROM snapshots'
        ).fetchone()[0]

    LATEST.parent.mkdir(
        parents=True, exist_ok=True
    )

    summary = {
        'started_at_utc': started,
        'finished_at_utc': finished,
        'discovered': len(addresses),
        'recorded': recorded,
        'skipped_short_interval': skipped,
        'open_paper_tokens_followed': len(pinned),
        'new_labels': new_labels,
        'total_labels': total_labels,
        'total_snapshots': total_snapshots,
        'errors': errors[:25],
        'note': (
            'Research only. Snapshot gaps '
            'and wallet risks remain unknown.'
        ),
    }

    LATEST.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False
        ) + '\n',
        encoding='utf-8'
    )

    print(json.dumps(
        summary, ensure_ascii=False
    ))

    if recorded == 0 and errors:
        raise RuntimeError(
            'No snapshots recorded; API errors detected'
        )


if __name__ == '__main__':
    run()
