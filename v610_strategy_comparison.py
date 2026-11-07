"""GTA6 Strategy B Comparison V1 — standalone read-only Streamlit panel.

GitHub B: data/v591/portfolio.json via existing _v593_read.
Radar B: existing SQLite table paper_v542_b via app.select, injected as callback.
No state changes. No trading, no DB writes. Sources are kept independent.
"""
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
import math

import pandas as pd
import streamlit as st


def _time(value):
    try:
        if not value:
            return None
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _amount(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_github(state):
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('GitHub portfolio heeft een onverwachte versie')
    closed = state.get('closed', {}).get('B')
    if not isinstance(closed, list):
        raise ValueError('GitHub B gesloten trades ontbreken')
    result = []
    for row in closed:
        if not isinstance(row, dict):
            continue
        result.append({
            'bron': 'GitHub B', 'mint': str(row.get('token') or '').strip(),
            'naam': str(row.get('name') or ''), 'pair': str(row.get('pair') or ''),
            'instap': _time(row.get('opened_at')), 'uitstap': _time(row.get('closed_at')),
            'instapprijs': None, 'uitstapprijs': None,
            'inzet_usd': 25.0, 'pnl_usd': _amount(row.get('pnl_usd')),
            'reden': str(row.get('reason') or ''),
        })
    return result


def normalize_radar(rows):
    result = []
    for row in rows:
        # Explicit column order from the SELECT below.
        (trade_id, address, name, pair, opened, closed, entry, exit_price,
         amount, pnl, reason, status) = row
        if str(status).upper() != 'CLOSED':
            continue
        result.append({
            'bron': 'Radar B', 'mint': str(address or '').strip(),
            'naam': str(name or ''), 'pair': str(pair or ''),
            'instap': _time(opened), 'uitstap': _time(closed),
            'instapprijs': _amount(entry), 'uitstapprijs': _amount(exit_price),
            'inzet_usd': _amount(amount), 'pnl_usd': _amount(pnl),
            'reden': str(reason or ''), 'id': trade_id,
        })
    return result


def stats(trades):
    vals = [r['pnl_usd'] for r in trades if r['pnl_usd'] is not None]
    wins = sum(x > 0 for x in vals)
    return {
        'trades': len(trades), 'met_resultaat': len(vals),
        'winrate_pct': round(100 * wins / len(vals), 1) if vals else None,
        'pnl_usd': round(sum(vals), 2) if vals else None,
        'mediaan_pnl': round(median(vals), 2) if vals else None,
    }


def overlap(github_trades, radar_trades):
    """Match same token mint, prefer nearest entry time, don't reuse trades."""
    by_mint = defaultdict(list)
    for idx, trade in enumerate(radar_trades):
        if trade['mint'] and trade['instap']:
            by_mint[trade['mint']].append((idx, trade))
    used = set()
    paired = []
    for git in sorted(github_trades, key=lambda t: t['instap'] or datetime.min.replace(tzinfo=timezone.utc)):
        if not git['mint'] or not git['instap']:
            continue
        choices = [(abs((rad['instap'] - git['instap']).total_seconds()), idx, rad)
                   for idx, rad in by_mint.get(git['mint'], []) if idx not in used]
        if not choices:
            continue
        seconds, idx, rad = min(choices, key=lambda item: item[0])
        used.add(idx)
        paired.append({
            'Token': git['naam'] or rad['naam'],
            'Mint': git['mint'],
            'GitHub instap UTC': git['instap'].strftime('%Y-%m-%d %H:%M:%S'),
            'Radar instap UTC': rad['instap'].strftime('%Y-%m-%d %H:%M:%S'),
            'Instapverschil min': round(seconds / 60, 1),
            'GitHub P/L $': git['pnl_usd'], 'Radar P/L $': rad['pnl_usd'],
            'GitHub reden': git['reden'], 'Radar reden': rad['reden'],
            'Zelfde pair': git['pair'] == rad['pair'] if git['pair'] and rad['pair'] else None,
        })
    return paired


def _display_rows(trades):
    return [{
        'Bron': t['bron'], 'Token': t['naam'], 'Mint': t['mint'],
        'Instap UTC': t['instap'].strftime('%Y-%m-%d %H:%M') if t['instap'] else 'Onbekend',
        'Uitstap UTC': t['uitstap'].strftime('%Y-%m-%d %H:%M') if t['uitstap'] else 'Onbekend',
        'P/L $': t['pnl_usd'], 'Exit': t['reden'],
    } for t in trades]


def v610_strategy_b_comparison(select_func):
    """Call in app.py as v610_strategy_b_comparison(select)."""
    from v593_dashboard import _v593_read, _v593_token

    st.divider()
    st.subheader('⚖️ Strategy Comparison V1 — GitHub B versus Radar B')
    st.caption('Alleen-lezen analyse. Twee onafhankelijke paperportfolio’s, geen echte uitvoeringen.')

    if st.button('🔄 B-vergelijking vernieuwen', key='v610_refresh'):
        _v593_read.clear()

    github_error, radar_error = None, None
    gh, radar = [], []
    try:
        gh = normalize_github(_v593_read('data/v591/portfolio.json', _v593_token()))
    except Exception as exc:
        github_error = type(exc).__name__ + ': ' + str(exc)
    try:
        rows = select_func('''
          SELECT id, address, name, pair_address, opened_at, closed_at,
                 entry_market, exit_market, amount_usd, pnl_usd, exit_reason, status
          FROM paper_v542_b WHERE UPPER(status) = 'CLOSED'
          ORDER BY opened_at
        ''')
        radar = normalize_radar(rows)
    except Exception as exc:
        radar_error = type(exc).__name__ + ': ' + str(exc)

    if github_error:
        st.error('GitHub B kon niet geladen worden: ' + github_error)
    if radar_error:
        st.error('Radar B kon niet geladen worden: ' + radar_error)
    if github_error or radar_error:
        st.info('De beschikbare bron blijft ongewijzigd. Controleer Streamlit Secrets en database-herstel.')
        return

    periods = [t['instap'] for t in gh + radar if t['instap']]
    if periods:
        min_dt = min(periods).date()
        max_dt = max(periods).date()
        period = st.date_input('Vergelijk trades geopend tussen (UTC)',
                               value=(min_dt, max_dt), min_value=min_dt, max_value=max_dt,
                               key='v610_period')
        if isinstance(period, (list, tuple)) and len(period) == 2:
            start, end = period
            gh = [t for t in gh if t['instap'] and start <= t['instap'].date() <= end]
            radar = [t for t in radar if t['instap'] and start <= t['instap'].date() <= end]

    a, b = st.columns(2)
    for column, label, trades in [(a, 'GitHub B', gh), (b, 'Radar B', radar)]:
        s = stats(trades)
        with column:
            st.markdown('**' + label + '**')
            st.metric('Afgesloten trades', s['trades'])
            st.metric('Gesimuleerde P/L', f"${s['pnl_usd']:+.2f}" if s['pnl_usd'] is not None else '—')
            st.metric('Winrate', f"{s['winrate_pct']:.1f}%" if s['winrate_pct'] is not None else '—')

    st.caption('Vergelijking gebruikt uitsluitend afgesloten papertrades die in de geselecteerde periode zijn geopend. Open posities en verschillende actieve scantijden zijn niet inbegrepen.')
    if not radar:
        st.warning('Geen afgesloten Radar B-trades in deze periode: winstverschil kan nog niet worden vastgesteld.')
    matched = overlap(gh, radar)
    st.subheader('Zelfde token, verschillende uitvoering')
    if matched:
        st.dataframe(pd.DataFrame(matched), hide_index=True, use_container_width=True)
        st.caption('Matching op mintadres en dichtstbijzijnde instaptijd; dit garandeert geen gelijkwaardige signalen of identieke marktcondities.')
    else:
        st.info('Geen overeenkomende mintadressen gevonden tussen de afgesloten trades in deze selectie.')

    with st.expander('Alle afgesloten B-trades per bron'):
        frame = pd.DataFrame(_display_rows(gh + radar))
        if not frame.empty:
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button('📥 Vergelijking als CSV', frame.to_csv(index=False).encode('utf-8'),
                               'strategy_b_comparison.csv', 'text/csv', key='v610_csv')
        else:
            st.info('Geen afgesloten trades om te tonen.')

    st.warning('Paper-P/L gebruikt gemodelleerde kosten; Jupiter-quotes zijn indicatief en historische uitvoerbare prijzen zijn niet achteraf bevestigd. Vergelijk geen niet-overlappende observatievensters als bewijs van betere strategie.')
