
"""GTA6 V5.6 standalone, unattended PUBLIC market-data collector.
No orders, wallets, paper trades, or changes to existing app databases.
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

DB = Path(os.getenv('LEARNING_DB', 'data/learning.db'))
LATEST = Path(os.getenv('LATEST_JSON', 'data/latest_scan.json'))
API = 'https://api.dexscreener.com'
SEARCHES = [
    'GTA6', 'GTA VI', 'Grand Theft Auto', 'Vice City',
    'Leonida', 'Lucia Camino', 'Jason Duval', 'Port Gellhorn'
]
MAX_KNOWN = 300
MAX_DISCOVERED = 240
KEYWORDS = [
    'gta6', 'gta 6', 'gta vi', 'gtavi', 'grand theft auto',
    'grandtheftauto', 'vice city', 'leonida', 'lucia camino',
    'jason duval', 'port gellhorn', 'raul bautista'
]
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'GTA6-Learning-Research/5.6',
    'Accept': 'application/json'
})


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def num(v):
    try:
        x = float(v)
        return x if x == x and abs(x) != float('inf') else None
    except (ValueError, TypeError, OverflowError):
        return None


def data_get(path, params=None):
    for attempt in range(3):
        try:
            r = SESSION.get(API + path, params=params, timeout=22)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f'HTTP {r.status_code}')
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=30)
    c.execute('PRAGMA busy_timeout=30000')
    c.execute('PRAGMA journal_mode=DELETE')

    c.execute('''CREATE TABLE IF NOT EXISTS tokens (
        address TEXT PRIMARY KEY,
        name TEXT,
        symbol TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS snapshots (
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

    c.execute('''CREATE INDEX IF NOT EXISTS snapshots_lookup
        ON snapshots(token_address,pair_address,scanned_at)''')

    c.execute('''CREATE TABLE IF NOT EXISTS outcomes (
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
            token_address,pair_address,baseline_at,horizon_minutes
        )
    )''')

    c.execute('''CREATE INDEX IF NOT EXISTS idx_snapshot_time
        ON snapshots(scanned_at)''')

    c.execute('''CREATE INDEX IF NOT EXISTS idx_outcome_baseline
        ON outcomes(baseline_at)''')

    c.execute('''CREATE TABLE IF NOT EXISTS runs (
        started_at TEXT PRIMARY KEY,
        finished_at TEXT,
        discovered INTEGER,
        snapshots INTEGER,
        error_count INTEGER,
        errors TEXT
    )''')

    c.commit()
    return c


def discovery(c):
    addresses = {}
    errors = []

    def add(addr, source):
        if isinstance(addr, str) and ADDRESS.fullmatch(addr):
            addresses.setdefault(addr, set()).add(source)

    for term in SEARCHES:
        try:
            response = data_get('/latest/dex/search', {'q': term})

            for pair in response.get('pairs') or []:
                if pair.get('chainId') != 'solana':
                    continue

                base = pair.get('baseToken') or {}
                title = (
                    f"{base.get('name', '')} "
                    f"{base.get('symbol', '')}"
                ).lower()

                if any(key in title for key in KEYWORDS):
                    add(base.get('address'), 'search')

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f'search {term}: {str(exc)[:140]}')

    for path in [
        '/token-profiles/latest/v1',
        '/token-boosts/latest/v1',
        '/token-boosts/top/v1'
    ]:
        try:
            response = data_get(path)

            for item in response if isinstance(response, list) else []:
                if item.get('chainId') != 'solana':
                    continue

                links = ' '.join(
                    str(x.get('url') or '')
                    for x in item.get('links') or []
                    if isinstance(x, dict)
                )

                title = (
                    f"{item.get('description') or ''} {links}"
                ).lower()

                if any(key in title for key in KEYWORDS):
                    add(item.get('tokenAddress'), 'profile')

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f'profile {path}: {str(exc)[:140]}')

    # Follow tokens with recent activity across scheduled runs.
    # This helps produce 15/30/60-minute labels for the same pool.
    active_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=3)
    ).isoformat(timespec='seconds')

    active = c.execute(
        '''SELECT s.token_address,MAX(s.scanned_at) AS last_active
           FROM snapshots s
           WHERE s.scanned_at >= ?
             AND s.price_usd > 0
             AND COALESCE(s.volume_m5,0) > 0
           GROUP BY s.token_address
           ORDER BY last_active DESC
           LIMIT ?''',
        (active_cutoff, MAX_KNOWN)
    ).fetchall()

    for addr, _ in active:
        add(addr, 'active-history')

    # Rotate inactive history instead of continuously
    # concentrating on the same inactive tokens.
    for (addr,) in c.execute(
        '''SELECT t.address
           FROM tokens t
           WHERE NOT EXISTS (
               SELECT 1
               FROM snapshots s
               WHERE s.token_address=t.address
                 AND s.scanned_at >= ?
                 AND COALESCE(s.volume_m5,0)>0
           )
           ORDER BY t.last_seen ASC
           LIMIT 80''',
        (active_cutoff,)
    ):
        add(addr, 'rotation')

    # Preserve active-history priorities if discovery
    # returns more than MAX_DISCOVERED addresses.
    if len(addresses) > MAX_DISCOVERED:
        active_addrs = [
            addr for addr in addresses
            if 'active-history' in addresses[addr]
        ]
        rest = [
            addr for addr in addresses
            if addr not in active_addrs
        ]
        kept = (active_addrs + rest)[:MAX_DISCOVERED]
        addresses = {
            addr: addresses[addr] for addr in kept
        }

    return addresses, errors


def fetch_pairs(addresses):
    found = {}
    errors = []

    for start in range(0, len(addresses), 30):
        group = addresses[start:start+30]

        try:
            response = data_get(
                '/tokens/v1/solana/' + ','.join(group)
            )

            if not isinstance(response, list):
                raise ValueError('Unexpected response type')

            for pair in response:
                if pair.get('chainId') != 'solana':
                    continue

                base = pair.get('baseToken') or {}
                addr = base.get('address')
                pid = pair.get('pairAddress')

                if (
                    addr not in group
                    or not isinstance(pid, str)
                    or not pid
                ):
                    continue

                # Select the most liquid pool for this token.
                previous = found.get(addr)

                liq = num(
                    (pair.get('liquidity') or {}).get('usd')
                ) or 0

                prev_liq = (
                    num(
                        (previous.get('liquidity') or {}).get('usd')
                    ) or 0
                ) if previous else -1

                if liq > prev_liq:
                    found[addr] = pair

        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(
                f'batch {start//30+1}: {str(exc)[:140]}'
            )

    return found, errors


def save_snapshots(c, pairs, sources, stamp):
    rows = 0
    current_ms = datetime.fromisoformat(stamp).timestamp() * 1000

    for addr, pair in pairs.items():
        base = pair.get('baseToken') or {}

        name = str(base.get('name') or '')[:200]
        symbol = str(base.get('symbol') or '')[:60]

        liq = num(
            (pair.get('liquidity') or {}).get('usd')
        )
        volume = num(
            (pair.get('volume') or {}).get('m5')
        )

        tx = (pair.get('txns') or {}).get('m5') or {}
        buys = int(num(tx.get('buys')) or 0)
        sells = int(num(tx.get('sells')) or 0)

        created = num(pair.get('pairCreatedAt'))
        age = (
            max(0, (current_ms - created)/3600000)
            if created else None
        )

        avg = (
            volume/(buys+sells)
            if volume is not None and buys+sells > 0
            else None
        )

        c.execute(
            '''INSERT INTO tokens(
                address,name,symbol,first_seen,last_seen
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                name=excluded.name,
                symbol=excluded.symbol,
                last_seen=excluded.last_seen''',
            (addr, name, symbol, stamp, stamp)
        )

        c.execute(
            '''INSERT OR IGNORE INTO snapshots(
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
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                addr,
                pair['pairAddress'],
                stamp,
                name,
                symbol,
                num(pair.get('priceUsd')),
                liq,
                volume,
                buys,
                sells,
                age,
                avg,
                ','.join(sorted(sources[addr]))
            )
        )

        rows += 1

    return rows


def build_outcomes(c):
    # Create labels only after sufficient time has elapsed.
    # Compare prices from the same token AND the same pool.
    # Allow the future observation to arrive up to 20 min late.
    # Each baseline receives one label per horizon.
    inserted = 0

    for horizon in (15, 30, 60):
        baselines = c.execute(
            '''SELECT
                s.token_address,
                s.pair_address,
                s.scanned_at,
                s.price_usd,
                s.liquidity_usd
            FROM snapshots s
            WHERE s.price_usd > 0
              AND julianday('now') - julianday(s.scanned_at)
                  >= ? / 1440.0
              AND NOT EXISTS (
                  SELECT 1
                  FROM outcomes o
                  WHERE o.token_address=s.token_address
                    AND o.pair_address=s.pair_address
                    AND o.baseline_at=s.scanned_at
                    AND o.horizon_minutes=?
              )
            ORDER BY s.scanned_at DESC
            LIMIT 2000''',
            (horizon, horizon)
        ).fetchall()

        for addr, pair, baseline, price, liq in baselines:
            future = c.execute(
                '''SELECT
                    scanned_at,
                    price_usd,
                    liquidity_usd
                FROM snapshots
                WHERE token_address=?
                  AND pair_address=?
                  AND price_usd > 0
                  AND julianday(scanned_at)
                      >= julianday(?) + ?/1440.0
                  AND julianday(scanned_at)
                      <= julianday(?) + (?+20)/1440.0
                ORDER BY scanned_at ASC
                LIMIT 1''',
                (
                    addr,
                    pair,
                    baseline,
                    horizon,
                    baseline,
                    horizon
                )
            ).fetchone()

            if future is None:
                continue

            observed, future_price, future_liq = future

            ret = 100 * (future_price/price - 1)

            liq_delta = (
                100 * (future_liq/liq - 1)
                if liq and future_liq is not None
                else None
            )

            c.execute(
                '''INSERT OR IGNORE INTO outcomes(
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
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    addr,
                    pair,
                    baseline,
                    horizon,
                    observed,
                    price,
                    future_price,
                    ret,
                    liq,
                    future_liq,
                    liq_delta,
                    int(ret <= -30),
                    (
                        int(liq_delta <= -50)
                        if liq_delta is not None
                        else None
                    )
                )
            )

            inserted += c.execute(
                'SELECT changes()'
            ).fetchone()[0]

    return inserted


def run():
    started = now()

    with connect() as c:
        addresses, errors = discovery(c)

        pairs, more_errors = fetch_pairs(list(addresses))
        errors += more_errors

        recorded = save_snapshots(
            c, pairs, addresses, started
        )

        labels = build_outcomes(c)

        finished = now()

        c.execute(
            'INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?)',
            (
                started,
                finished,
                len(addresses),
                recorded,
                len(errors),
                json.dumps(errors)
            )
        )

        c.commit()

        outcome_total = c.execute(
            'SELECT COUNT(*) FROM outcomes'
        ).fetchone()[0]

        snapshot_total = c.execute(
            'SELECT COUNT(*) FROM snapshots'
        ).fetchone()[0]

    LATEST.parent.mkdir(parents=True, exist_ok=True)

    with connect() as c:
        past_60 = (
            datetime.now(timezone.utc)
            - timedelta(minutes=60)
        ).isoformat(timespec='seconds')

        last_hour = c.execute(
            '''SELECT COUNT(*)
               FROM snapshots
               WHERE scanned_at >= ?''',
            (past_60,)
        ).fetchone()[0]

        eligible = c.execute(
            '''SELECT COUNT(*)
               FROM snapshots
               WHERE price_usd > 0
                 AND volume_m5 > 0
                 AND liquidity_usd >= 10000'''
        ).fetchone()[0]

        covered = c.execute(
            '''SELECT COUNT(
                DISTINCT token_address || ':' || pair_address
            )
            FROM snapshots
            WHERE scanned_at >= ?''',
            (past_60,)
        ).fetchone()[0]

        last_runs = c.execute(
            '''SELECT started_at
               FROM runs
               ORDER BY started_at DESC
               LIMIT 2'''
        ).fetchall()

    gap = None

    if len(last_runs) >= 2:
        gap = round(
            (
                datetime.fromisoformat(last_runs[0][0])
                - datetime.fromisoformat(last_runs[1][0])
            ).total_seconds() / 60,
            1
        )

    summary = {
        'started_at_utc': started,
        'finished_at_utc': finished,
        'discovered': len(addresses),
        'recorded': recorded,
        'new_labels': labels,
        'total_labels': outcome_total,
        'total_snapshots': snapshot_total,
        'last_hour_snapshots': last_hour,
        'eligible_liquid_snapshots': eligible,
        'last_hour_pairs': covered,
        'minutes_since_previous_run': gap,
        'errors': errors[:25],
        'note': (
            'Research only. Labels require a valid future '
            'price of the same pool at 15/30/60m '
            '(up to 20m late). GitHub cron is best-effort; '
            'no guaranteed 5-minute schedule.'
        )
    }

    LATEST.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False
        ) + '\n',
        encoding='utf-8'
    )

    print(json.dumps(summary, ensure_ascii=False))

    if recorded == 0 and errors:
        raise RuntimeError(
            'No snapshots recorded; API errors detected'
        )


if __name__ == '__main__':
    run()
