"""Execution Accuracy Monitor V1 — observe current paper positions; NEVER trade.

Compares contemporaneous Jupiter USDC sell quotes to the fixed-fee/slippage
paper mark for existing GitHub B and B2 positions, using identical latest
snapshot timestamps. Saves bounded observational history, not executions.
Does not reconstruct expired historical quotes or rewrite any portfolios.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from radar_quote_monitor import (D, USDC, USDC_DECIMALS, get_decimals, quote,
                                 token_amount_to_raw, sanitize_error, MINT_PATTERN)
from v591_paper_engine import STAKE, FEE, SLIP

MAX_SNAPSHOT_AGE_MIN = 12
MAX_SAMPLES = 2500


def number(v):
    try:
        x = D(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_time(value):
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def load_positions(b_path, b2_path):
    b = json.loads(b_path.read_text(encoding='utf-8'))
    b2 = json.loads(b2_path.read_text(encoding='utf-8'))
    if b.get('version') != 1 or b2.get('version') != 1:
        raise ValueError('INVALID_PORTFOLIO_VERSION')
    if not isinstance(b.get('open'), dict) or not isinstance(b2.get('open'), dict):
        raise ValueError('INVALID_OPEN_POSITIONS')
    if not isinstance(b['open'].get('B'), dict):
        raise ValueError('INVALID_B_POSITIONS')
    result = []
    for strategy, positions in [('B', b['open']['B']), ('B2', b2['open'])]:
        for mint, p in positions.items():
            if isinstance(p, dict) and MINT_PATTERN.fullmatch(mint):
                result.append((strategy, mint, p))
    return result


def latest_observation(conn, mint, pair):
    return conn.execute('''SELECT scanned_at, price_usd, liquidity_usd
       FROM snapshots WHERE token_address=? AND pair_address=?
       ORDER BY scanned_at DESC LIMIT 1''', (mint, pair)).fetchone()


def compare(db, b_path, b2_path, key, rpc_url, now):
    rows = []
    positions = load_positions(b_path, b2_path)
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        with requests.Session() as session:
            decimals_cache = {}
            for strategy, mint, p in positions:
                r = {'strategy':strategy, 'mint':mint, 'name':p.get('name'),
                     'pair':p.get('pair'), 'opened_at':p.get('opened_at'),
                     'snapshot_utc':None, 'snapshot_age_min':None,
                     'market_liquidity_usd':None, 'paper_exit_usdc':None,
                     'jupiter_exit_usdc':None, 'paper_pnl_usd':None,
                     'jupiter_pnl_usd':None, 'quote_minus_paper_usdc':None,
                     'quote_minus_paper_pct_of_stake':None,
                     'status':'UNAVAILABLE','reason':None}
                try:
                    pair = p.get('pair')
                    if not isinstance(pair, str):
                        raise ValueError('MISSING_PAIR')
                    obs = latest_observation(conn, mint, pair)
                    if not obs:
                        r['reason'] = 'NO_SNAPSHOT'
                        rows.append(r)
                        continue
                    stamp, price, liq = obs
                    r['snapshot_utc'] = stamp
                    age = (now - parse_time(stamp)).total_seconds()/60
                    r['snapshot_age_min'] = round(age, 2)
                    r['market_liquidity_usd'] = liq
                    price, qty = number(price), number(p.get('quantity'))
                    if price is None or price <= 0 or qty is None or qty <= 0:
                        r['reason'] = 'INVALID_MARK_OR_QUANTITY'
                        rows.append(r)
                        continue
                    # Same fixed fee/slippage assumptions as the paper engine.
                    paper_exit = qty*price*(D(1)-D(str(SLIP)))*(D(1)-D(str(FEE)))
                    paper_pnl = paper_exit - D(str(STAKE))
                    r['paper_exit_usdc'] = str(round(paper_exit, 6))
                    r['paper_pnl_usd'] = str(round(paper_pnl, 6))
                    if age < 0 or age > MAX_SNAPSHOT_AGE_MIN:
                        r['reason'] = 'SNAPSHOT_STALE'
                        rows.append(r)
                        continue
                    if not key:
                        r['reason'] = 'JUPITER_API_KEY_MISSING'
                        rows.append(r)
                        continue
                    if mint not in decimals_cache:
                        decimals_cache[mint] = get_decimals(session, mint, rpc_url)
                    raw = token_amount_to_raw(qty, decimals_cache[mint])
                    exitq = quote(session, key, mint, USDC, raw)
                    if exitq.get('error_code') or exitq.get('quote_only') is not True:
                        r['reason'] = 'JUPITER_ROUTE_ERROR'
                        rows.append(r)
                        continue
                    actual = D(exitq['output_raw']) / D(10**USDC_DECIMALS)
                    diff = actual-paper_exit
                    r['jupiter_exit_usdc'] = str(round(actual, 6))
                    r['jupiter_pnl_usd'] = str(round(actual-D(str(STAKE)), 6))
                    r['quote_minus_paper_usdc'] = str(round(diff, 6))
                    r['quote_minus_paper_pct_of_stake'] = str(round(diff/D(str(STAKE))*100, 4))
                    r['status'] = 'QUOTE_AVAILABLE'
                except Exception as exc:
                    r['reason'] = sanitize_error(exc)
                rows.append(r)
    return rows


def persist_history(path, now, rows):
    if path.is_file():
        history = json.loads(path.read_text(encoding='utf-8'))
        if history.get('version') != 1 or not isinstance(history.get('samples'), list):
            raise ValueError('INVALID_EXISTING_HISTORY_REFUSE_OVERWRITE')
    else:
        history = {'version':1,'mode':'READ_ONLY_QUOTE_COMPARISON','samples':[]}
    # Each timestamp+strategy+token has only one observation.
    existing = {(x.get('checked_utc'),x.get('strategy'),x.get('mint'))
                for x in history['samples'] if isinstance(x,dict)}
    stamp = now.isoformat(timespec='seconds')
    for row in rows:
        key = (stamp,row['strategy'],row['mint'])
        if key not in existing:
            history['samples'].append({'checked_utc':stamp,**row})
            existing.add(key)
    history['samples'] = history['samples'][-MAX_SAMPLES:]
    history['updated_utc'] = stamp
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(history,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    tmp.replace(path)
    return len(history['samples'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='data/learning.db')
    p.add_argument('--b',default='data/v591/portfolio.json')
    p.add_argument('--b2',default='data/v611/b2_portfolio.json')
    p.add_argument('--history',default='data/v612/execution_accuracy_history.json')
    p.add_argument('--report',default='data/v612/execution_accuracy.json')
    a=p.parse_args()
    now=datetime.now(timezone.utc)
    report={'version':'1.0','mode':'READ_ONLY_EXECUTION_ACCURACY',
        'checked_utc':now.isoformat(timespec='seconds'),
        'status':'ERROR','live_orders_enabled':False,'positions':[],
        'notes':['Only currently open B/B2 paper positions; no historical fill reconstruction.',
                 'Jupiter quote-only is indicative; not a fill or an executable guarantee.',
                 'Paper exits use the fixed paper-engine model at latest observed price.',
                 'Quotes are requested NOW; 12-minute freshness threshold for marks.',
                 'Quote-only results exclude network/priority fees and account setup costs.',
                 'No trading rules, portfolios, database, or live trades changed.']}
    try:
        rpc=os.getenv('SOLANA_RPC_URL','').strip() or 'https://api.mainnet-beta.solana.com'
        if not rpc.startswith('https://'):
            raise ValueError('RPC_MUST_BE_HTTPS')
        rows=compare(Path(a.db),Path(a.b),Path(a.b2),os.getenv('JUPITER_API_KEY','').strip(),rpc,now)
        report['positions']=rows
        report['positions_count']=len(rows)
        report['quotes_available']=sum(r['status']=='QUOTE_AVAILABLE' for r in rows)
        report['missing_quotes']=len(rows)-report['quotes_available']
        report['history_samples']=persist_history(Path(a.history),now,rows)
        report['status']='OK' if not rows or report['missing_quotes']==0 else 'PARTIAL'
    except Exception as exc:
        report['error']=sanitize_error(exc) if not isinstance(exc, OSError) else 'FILE_ERROR'
    dest=Path(a.report)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'positions':report.get('positions_count',0),
                      'quotes_available':report.get('quotes_available',0)},ensure_ascii=False))

if __name__=='__main__':
    main()
