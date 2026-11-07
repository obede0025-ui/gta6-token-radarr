"""GTA6 Pre-Trade Safety V2 — read-only shadow candidate gate.

Replays latest pair observations through original A/B/C momentum conditions,
then checks Jupiter quote-only roundtrip for qualifying candidates. Does NOT
alter portfolios, approve live orders, or sign transactions. Designed as a
reporting step after the existing engine; it is NOT yet an inline pre-entry gate.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from radar_quote_monitor import (USDC, USDC_DECIMALS, D, quote,
                                token_amount_to_raw, sanitize_error,
                                MINT_PATTERN)
from v591_paper_engine import recent_pair_history

MAX_AGE_MIN = D('12')
MAX_ROUNDTRIP_LOSS = D('3')
MIN_LIQ = D('20000')
STAKE = D('25')
MAX_CANDIDATES = 12


def parse_time(s):
    dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def finite(v):
    try:
        x = D(str(v))
        return x if x.is_finite() else None
    except (ValueError, ArithmeticError, TypeError):
        return None


def select_candidates(conn, state, when, limit=MAX_CANDIDATES):
    # Get most recent 3 observations for each mint+pair, preserving exact pair identity.
    rows = conn.execute('''
        SELECT token_address, pair_address, scanned_at, name, price_usd,
               liquidity_usd, volume_m5, buys_m5, sells_m5,
               pair_age_hours, average_trade_usd
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY token_address,pair_address ORDER BY scanned_at DESC
            ) AS position
            FROM snapshots WHERE scanned_at >= ?
        ) WHERE position <= 3
        ORDER BY token_address, pair_address, scanned_at
    ''', ((when - __import__('datetime').timedelta(hours=2)).isoformat(timespec='seconds'),)).fetchall()
    groups = {}
    for row in rows:
        groups.setdefault((row[0], row[1]), []).append(row)
    candidates = []
    for (mint, pair), obs in groups.items():
        if not MINT_PATTERN.fullmatch(mint) or len(obs) < 2:
            continue
        last = obs[-1]
        stamp, name, price, liq, vol, buys, sells, pair_age, avg = last[2:]
        try:
            elapsed = (when - parse_time(stamp)).total_seconds() / 60
        except (TypeError, ValueError):
            continue
        if elapsed < 0 or elapsed > float(MAX_AGE_MIN):
            continue
        p, liquidity, v, age, avg_trade = map(finite, (price, liq, vol, pair_age, avg))
        if p is None or p <= 0 or v is None:
            continue
        b, s = finite(buys) or D(0), finite(sells) or D(0)
        micro = b + s >= 100 and avg_trade is not None and avg_trade < 3
        if liquidity is None or liquidity < 10000 or v < 250 or age is None or not (0 <= age <= 24) or b <= s:
            continue
        history = [[x[2], x[4], x[6]] for x in obs[:-1]]
        consecutive = recent_pair_history(history, stamp)
        flags = []
        if len(consecutive) >= 2:
            o, prior = consecutive[-2:]
            p1, p2, v1, v2 = map(finite, (o[1], prior[1], o[2], prior[2]))
            if None not in (p1, p2, v1, v2) and p1 > 0 and p2 > 0:
                if p1 < p2 < p and v1 < v2 < v and p / p1 >= D('1.02') and not micro:
                    flags.append('A')
                if p / p1 >= D('1.05') and p > p2:
                    flags.append('B')
        if len(consecutive) >= 1:
            prior = consecutive[-1]
            prev_price, prev_vol = finite(prior[1]), finite(prior[2])
            if prev_price is not None and prev_price > 0 and prev_vol is not None and prev_vol > 0:
                gain = p / prev_price - 1
                if (D('0.015') <= gain <= D('0.15') and v >= prev_vol * D('1.5')
                        and b >= 3 and not micro and 0 <= age <= 12):
                    flags.append('C')
        for strategy in flags:
            if mint in (state['traded'].get(strategy) or []):
                continue
            if mint in (state['open'].get(strategy) or {}):
                continue
            if len(state['open'].get(strategy) or {}) >= 3:
                continue
            candidates.append({'mint':mint,'pair':pair,'name':name,'strategy':strategy,
                               'scanned_at_utc':stamp,'market_age_min':round(elapsed,2),
                               'price_usd':str(p),'liquidity_usd':str(liquidity),
                               'volume_5m_usd':str(v)})
    candidates.sort(key=lambda x:(x['scanned_at_utc'], finite(x['volume_5m_usd'])), reverse=True)
    return candidates[:limit]


def evaluate_candidate(candidate, session, key):
    reasons = []
    liquidity = finite(candidate['liquidity_usd'])
    if liquidity is None or liquidity < MIN_LIQ:
        reasons.append('LIQUIDITY_BELOW_20000')
    if candidate['market_age_min'] > float(MAX_AGE_MIN):
        reasons.append('MARKET_DATA_STALE')
    result = dict(candidate, quote_gate='BLOCK', reasons=reasons,
                  roundtrip_pct=None, live_trade_approval='BLOCK')
    if not key:
        reasons.append('JUPITER_API_KEY_MISSING')
        return result
    try:
        raw = token_amount_to_raw(STAKE, USDC_DECIMALS)
        buy = quote(session, key, USDC, candidate['mint'], raw)
        sell = quote(session, key, candidate['mint'], USDC, buy['output_raw'])
        if not buy['quote_only'] or not sell['quote_only'] or buy['error_code'] or sell['error_code']:
            reasons.append('ROUTE_INVALID')
        out = D(sell['output_raw']) / D(10 ** USDC_DECIMALS)
        pct = (out / STAKE - 1) * D('100')
        result['roundtrip_pct'] = str(pct.quantize(D('0.001')))
        if pct < -MAX_ROUNDTRIP_LOSS:
            reasons.append('ROUNDTRIP_COST_TOO_HIGH')
    except Exception as exc:
        reasons.append('QUOTE_' + sanitize_error(exc))
    result['quote_gate'] = 'PASS' if not reasons else 'BLOCK'
    return result


def report(db_path, state_path, key=None, when=None):
    when = when or datetime.now(timezone.utc)
    output = {'version':'2.0','mode':'SHADOW_PRETRADE_READ_ONLY',
              'checked_utc':when.isoformat(timespec='seconds'), 'status':'BLOCKED',
              'live_orders_enabled':False, 'candidates':[],
              'note':'No order signing. Shadow signal replay only; not an inline pre-entry gate.'}
    if not Path(db_path).is_file() or not Path(state_path).is_file():
        output['error']='MISSING_DATABASE_OR_STATE'
        return output
    try:
        state=json.loads(Path(state_path).read_text(encoding='utf-8'))
        if state.get('version') != 1 or not all(isinstance(state.get(k),dict) for k in ('traded','open','closed')):
            raise ValueError('INVALID_STATE')
        with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise ValueError('INVALID_DB')
            candidates=select_candidates(conn,state,when)
        with requests.Session() as session:
            output['candidates']=[evaluate_candidate(c,session,key) for c in candidates]
        output['candidate_count']=len(candidates)
        output['quote_gate_passed']=sum(c['quote_gate']=='PASS' for c in output['candidates'])
        output['quote_gate_blocked']=len(candidates)-output['quote_gate_passed']
        output['status']='OK' if key else 'NOT_CONFIGURED'
    except (OSError,ValueError,sqlite3.Error,TypeError) as exc:
        output['error']=type(exc).__name__
    return output


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='data/learning.db')
    p.add_argument('--state',default='data/v591/portfolio.json')
    a=p.parse_args()
    print(json.dumps(report(a.db,a.state,os.getenv('JUPITER_API_KEY','').strip()),indent=2,ensure_ascii=False))


if __name__=='__main__':
    main()
