"""GTA6 Radar — Gemiste-kansen-audit V1 (uitsluitend lezen).

Meet lange intervallen tussen opeenvolgende observaties van hetzelfde pair.
Een koerssprong tussen zulke observaties is een potentiële blinde vlek,
GEEN bewijs dat er tussentijds een uitvoerbare instap bestond.
Vereist alleen de bestaande data/learning.db; geen marktorders/netwerk.
"""
import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = '1.0'
POST_COOLDOWN_UTC = '2026-11-07T17:10:00+00:00'
GAP_THRESHOLD_MIN = 20.0
PRICE_JUMP_THRESHOLD_PCT = 20.0


def timestamp(s):
    dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def inspect_database(database, hours=24):
    database = Path(database)
    if not database.is_file():
        raise FileNotFoundError(f'Learning database ontbreekt: {database}')
    uri = database.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as con:
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Learning database integriteitscontrole mislukt')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'snapshots', 'tokens'}.issubset(tables):
            raise RuntimeError('Learning database mist snapshots of tokens')
        last_stamp = con.execute('SELECT MAX(scanned_at) FROM snapshots').fetchone()[0]
        if not last_stamp:
            raise RuntimeError('Geen snapshots beschikbaar')
        end = timestamp(last_stamp)
        start = end - timedelta(hours=hours)
        # Eén observatie voor het begin van het venster mag niet als lange gat
        # worden behandeld; we tellen uitsluitend paren binnen het venster.
        rows = con.execute('''
            SELECT s.token_address, s.pair_address, s.scanned_at, s.name,
                   s.price_usd, s.volume_m5, s.liquidity_usd
            FROM snapshots s
            WHERE s.scanned_at >= ? AND s.scanned_at <= ?
            ORDER BY s.token_address, s.pair_address, s.scanned_at
        ''', (start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds'))).fetchall()

    counts = Counter()
    by_period = {'before_cooldown': Counter(), 'after_cooldown': Counter()}
    candidates = []
    prev = {}
    boundary = timestamp(POST_COOLDOWN_UTC)
    unique_pairs = set()
    for mint, pair, stamp, name, price, vol, liq in rows:
        key = (mint, pair)
        unique_pairs.add(key)
        t = timestamp(stamp)
        old = prev.get(key)
        prev[key] = (t, price, vol)
        if old is None:
            continue
        older_t, older_price, older_vol = old
        delta = (t - older_t).total_seconds() / 60.0
        if delta <= 0:
            continue
        group = ('after_cooldown' if older_t >= boundary else
                 'before_cooldown' if t < boundary else None)
        counts['intervals'] += 1
        if group:
            by_period[group]['intervals'] += 1
        if delta > GAP_THRESHOLD_MIN:
            counts['long_gaps'] += 1
            if group:
                by_period[group]['long_gaps'] += 1
            if isinstance(price, (float, int)) and isinstance(older_price, (float, int)) and price > 0 and older_price > 0:
                jump = 100 * (price / older_price - 1)
                if jump >= PRICE_JUMP_THRESHOLD_PCT:
                    counts['upward_gaps'] += 1
                    if group:
                        by_period[group]['upward_gaps'] += 1
                    candidates.append({
                        'token': mint, 'pair': pair, 'name': name,
                        'from_utc': older_t.isoformat(timespec='seconds'),
                        'to_utc': t.isoformat(timespec='seconds'),
                        'gap_minutes': round(delta, 2),
                        'observed_price_change_pct': round(jump, 2),
                        'volume_m5_at_return': vol,
                        'liquidity_usd_at_return': liq,
                        'classification': 'POTENTIELE_BLINDE_VLEK',
                    })
    candidates.sort(key=lambda x: (-x['observed_price_change_pct'], -x['gap_minutes']))

    def stats(c):
        intervals = c['intervals']
        return {
            'intervals': intervals,
            'gaps_over_20_min': c['long_gaps'],
            'gap_rate_pct': round(c['long_gaps'] * 100 / intervals, 2) if intervals else None,
            'gaps_with_observed_rise_over_20pct': c['upward_gaps'],
        }

    return {
        'version': VERSION,
        'mode': 'READ_ONLY_RETROSPECTIVE',
        'analysis_window_hours': hours,
        'observations': len(rows),
        'unique_pairs': len(unique_pairs),
        'window_start_utc': start.isoformat(timespec='seconds'),
        'window_end_utc': end.isoformat(timespec='seconds'),
        'cooldown_boundary_utc': POST_COOLDOWN_UTC,
        'overall': stats(counts),
        'before_cooldown': stats(by_period['before_cooldown']),
        'after_cooldown': stats(by_period['after_cooldown']),
        'top_potential_blind_spots': candidates[:30],
        'blind_spot_count': len(candidates),
        'methodology': ('Vergelijkt uitsluitend opeenvolgende, opgeslagen marktmetingen van exact hetzelfde token/pair. '
                        'Meer dan 20 minuten tussen metingen plus een gemeten prijsstijging van minstens 20% '
                        'markeert een potentiële blinde vlek. Tokenbewegingen zonder latere meting blijven onbekend.'),
        'limitations': ('Geen realtime ontdekking van niet-gevolgde tokens. Niet aantoonbaar veroorzaakt door cooldown. '
                        'Geen bewijs van verhandelbaarheid, slippage of winst. Kleine pre/post-steekproeven niet causaal interpreteren.'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='data/learning.db')
    ap.add_argument('--hours', type=int, default=24)
    args = ap.parse_args()
    if not 1 <= args.hours <= 168:
        ap.error('--hours moet tussen 1 en 168 zijn')
    print(json.dumps(inspect_database(args.db, args.hours), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
