"""GTA6 B2 independent shadow-paper engine. No wallet or real trades.

A/B/C original portfolio and learning DB are READ ONLY. B2 keeps its own
state under data/v611. It starts at the newest observed row on the FIRST RUN,
so it NEVER retroactively creates trades. Subsequent runs process new snapshots,
using only latest scan observations for new quoted entries (fail closed).
Quote checks are contemporaneous indications, not guaranteed executable fills.
"""
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

from radar_pretrade_safety import evaluate_candidate
from v591_paper_engine import (STAKE, FEE, SLIP, MIN_VOL, MAX_POSITIONS,
                               recent_pair_history, num, utc)

MAX_LOOKBACK_AGE_SEC = 150
MIN_LIQ = 20_000.0
MAX_OPEN = MAX_POSITIONS


def read_state(path):
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if (data.get('version') != 1 or not isinstance(data.get('open'), dict)
                or not isinstance(data.get('closed'), list)
                or not isinstance(data.get('traded'), list)
                or not isinstance(data.get('cursor'), list)
                or len(data['cursor']) != 3):
            raise ValueError('Invalid existing B2 state; refusing to overwrite it')
        return data
    return None


def observations(conn, cursor):
    # Same ordering as original B, ensure strict cursor and no duplicate replay.
    rows = conn.execute('''SELECT scanned_at,token_address,pair_address,name,
            price_usd,liquidity_usd,volume_m5,buys_m5,sells_m5,
            pair_age_hours,average_trade_usd
            FROM snapshots WHERE scanned_at >= ?
            ORDER BY scanned_at,token_address,pair_address''', (cursor[0],))
    for row in rows:
        if list(row[:3]) > cursor:
            yield row


def signal_b(conn, stamp, mint, pair, price, volume):
    if not price or price <= 0 or volume is None or volume < MIN_VOL:
        return False
    # Only observations PRIOR to the candidate timestamp, not future samples.
    older = conn.execute('''SELECT scanned_at,price_usd,volume_m5
       FROM snapshots WHERE token_address=? AND pair_address=? AND scanned_at<?
       ORDER BY scanned_at DESC LIMIT 3''', (mint,pair,stamp)).fetchall()
    older.reverse()
    history = [[t,p,v] for t,p,v in older]
    consecutive = recent_pair_history(history, stamp)
    if len(consecutive) < 2:
        return False
    old, previous = consecutive[-2:]
    p1,p2,v1,v2 = map(num,(old[1],previous[1],old[2],previous[2]))
    return bool(p1 and p1>0 and p2 and p2>0 and v1 is not None
                and v2 is not None and price/p1 >= 1.05 and price > p2)


def initial_cursor(conn):
    row = conn.execute('''SELECT scanned_at,token_address,pair_address
       FROM snapshots ORDER BY scanned_at DESC, token_address DESC,
       pair_address DESC LIMIT 1''').fetchone()
    if not row:
        raise ValueError('Cannot initialize B2: no snapshots')
    return list(row)


def run(db, state_path, report_path, api_key, now=None):
    now = now or datetime.now(timezone.utc)
    if not db.is_file():
        raise FileNotFoundError('Existing learning DB not found')
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Invalid learning DB')
        state = read_state(state_path)
        bootstrap = state is None
        if bootstrap:
            state = {'version':1, 'cursor':initial_cursor(conn),'open':{},
                     'closed':[], 'traded':[],
                     'started_utc':now.isoformat(timespec='seconds')}
        counts = Counter()
        decisions = []
        seen = set(state['traded'])
        # Process sequentially; exit updates on every incoming observation.
        rows = [] if bootstrap else list(observations(conn,state['cursor']))
        with requests.Session() as session:
            for row in rows:
                stamp,mint,pair,name,price,liq,vol,buys,sells,age,avg = row
                state['cursor'] = list(row[:3])
                counts['observations'] += 1
                price,liq,vol,age,avg = map(num,(price,liq,vol,age,avg))
                buys,sells = num(buys) or 0,num(sells) or 0
                if price is None or price <= 0:
                    continue
                held = state['open'].get(mint)
                if held and held['pair'] == pair:
                    change = price / held['entry_market'] - 1
                    hours_held = (utc(stamp)-utc(held['opened_at'])).total_seconds()/3600
                    exit_reason = ('TAKE_PROFIT' if change >= .30 else
                                   'STOP_LOSS' if change <= -.15 else
                                   'TIME_EXIT' if hours_held >= 4 else None)
                    if exit_reason and liq is not None and liq >= 10000:
                        proceeds = held['quantity']*price*(1-SLIP)*(1-FEE)
                        state['closed'].append({'token':mint,'name':held['name'],
                            'pair':pair,'opened_at':held['opened_at'],
                            'closed_at':stamp,'reason':exit_reason,
                            'pnl_usd':round(proceeds-STAKE,5),
                            'note':'Simulated fixed fees/slippage; not actual Jupiter exit quote'})
                        del state['open'][mint]
                        counts['closed'] += 1
                if mint in seen or mint in state['open']:
                    continue
                # Only truly recent rows: never quote an old observation using
                # today's liquidity and pretend the quote was available back then.
                observation_age = (now-utc(stamp)).total_seconds()
                if observation_age < 0 or observation_age > MAX_LOOKBACK_AGE_SEC:
                    counts['stale_entry_skips'] += 1
                    continue
                if not signal_b(conn,stamp,mint,pair,price,vol):
                    continue
                if age is None or not 0 <= age <= 24 or buys <= sells or liq is None or liq < 10000:
                    counts['market_filtered'] += 1
                    continue
                counts['b_signals'] += 1
                if len(state['open']) >= MAX_OPEN:
                    counts['max_positions'] += 1
                    continue
                candidate = {'mint':mint,'pair':pair,'name':name or 'Onbekend',
                    'strategy':'B','scanned_at_utc':stamp,
                    'market_age_min':round(observation_age/60,2),
                    'price_usd':str(price),'liquidity_usd':str(liq),
                    'volume_5m_usd':str(vol)}
                verdict = evaluate_candidate(candidate,session,api_key)
                decisions.append({'token':mint,'name':name,'stamp':stamp,
                    'quote_gate':verdict['quote_gate'],
                    'reasons':verdict['reasons'],
                    'roundtrip_pct':verdict['roundtrip_pct']})
                if verdict['quote_gate'] != 'PASS':
                    counts['quote_blocked'] += 1
                    continue
                # Uses same paper fee/slippage model as original B, not
                # Jupiter quote outputs. Trades are still simulated.
                qty = STAKE*(1-FEE)/(price*(1+SLIP))
                state['open'][mint]={'name':name or 'Onbekend','pair':pair,
                    'opened_at':stamp,'entry_market':price,'quantity':qty,
                    'quote_roundtrip_pct':verdict['roundtrip_pct']}
                seen.add(mint)
                counts['opened'] += 1
        state['traded'] = sorted(seen)
    # Persist only after successfully processing all rows. Atomic replace.
    state_path.parent.mkdir(parents=True,exist_ok=True)
    temp = state_path.with_suffix('.tmp')
    temp.write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    temp.replace(state_path)
    closed = state['closed']
    report = {'version':'1.0','mode':'B2_SHADOW_PAPER_ONLY',
       'generated_utc':now.isoformat(timespec='seconds'),
       'initialized_this_run':bootstrap,'cursor':state['cursor'],
       'run':dict(counts),'recent_decisions':decisions[-15:],
       'portfolio':{'open':len(state['open']),'closed':len(closed),
          'realized_pnl_usd':round(sum(float(t['pnl_usd']) for t in closed),4),
          'wins':sum(t['pnl_usd']>0 for t in closed)},
       'rules':{'strategy':'B momentum identical thresholds',
          'min_liquidity_usd':MIN_LIQ,'maximum_roundtrip_loss_pct':3,
          'take_profit_pct':30,'stop_loss_pct':15,'time_exit_hours':4},
       'limitations':['Not a live-order safety approval.',
          'No contract/holder safety gating yet; requires separate audit.',
          'Jupiter quotes are indications, not settled transactions.',
          'Exit P/L uses simulated market price and fixed fees, not sale quotes.',
          'No automatic trade replay before the first run; B2 cohort starts now.',
          'B2 shares market observations, but paper portfolios remain separate.']}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='data/learning.db')
    p.add_argument('--state',default='data/v611/b2_portfolio.json')
    p.add_argument('--report',default='data/v611/b2_report.json')
    args=p.parse_args()
    result=run(Path(args.db),Path(args.state),Path(args.report),os.getenv('JUPITER_API_KEY','').strip())
    print(json.dumps({'mode':result['mode'],'run':result['run'],
                      'portfolio':result['portfolio']},ensure_ascii=False))

if __name__=='__main__':
    main()
