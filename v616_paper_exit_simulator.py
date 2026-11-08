"""V6.16 — independent read-only exit valuation for local Radar B.

Uses the V6.14 quote results already in Streamlit session_state. Never creates
quotes, runs trades, changes SQLite, or rewrites historical paper performance.
An indicative Jupiter quote is NOT an executed sale or guaranteed fill.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

MAX_QUOTE_AGE_MINUTES = 12
MAX_MARK_AGE_MINUTES = 12


def _d(value):
    try:
        x = Decimal(str(value))
        return x if x.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _utc(value):
    try:
        if not value:
            return None
        x = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate_position(trade, sample, checked_at, now):
    """Pure calculation of same-position quote vs current paper mark."""
    (tid, mint, name, entry_pair, stake, qty, fee, slip,
     last_mark, last_mark_at, exit_reason) = trade
    stake, qty, fee, slip, mark = map(_d, (stake, qty, fee, slip, last_mark))
    checked = _utc(checked_at)
    checked_age = ((now - checked).total_seconds() / 60) if checked else None
    mark_stamp = _utc(last_mark_at)
    mark_age = ((now - mark_stamp).total_seconds() / 60) if mark_stamp else None
    row = {
        'Trade-ID': tid, 'Token': name, 'Mint': mint,
        'Exitstatus DB': exit_reason or '—',
        'Quoteleeftijd min': round(checked_age, 2) if checked_age is not None else None,
        'Positiemarkleeftijd min': round(mark_age, 2) if mark_age is not None else None,
        'Paper verkoopwaarde $': None, 'Jupiter verkoopwaarde $': None,
        'Paper P/L $': None, 'Jupiter P/L $': None,
        'Verschil Jupiter - paper $': None,
        'Jupiter / paper %': None,
        'Beoordeling': 'NIET_VERGELIJKBAAR', 'Reden': '',
    }
    if stake is None or stake <= 0 or qty is None or qty <= 0:
        row['Reden'] = 'POSITIE_ONGELDIG'
        return row
    if not sample or sample.get('Mint') != mint:
        row['Reden'] = 'QUOTE_VOOR_DEZE_POSITIE_ONTBREEKT'
        return row
    if sample.get('Quotecontrole') != 'QUOTE_BESCHIKBAAR':
        row['Reden'] = str(sample.get('Reden') or 'QUOTE_NIET_BESCHIKBAAR')
        return row
    if checked_age is None or not 0 <= checked_age <= MAX_QUOTE_AGE_MINUTES:
        row['Reden'] = 'QUOTE_VEROUDERD'
        return row
    if sample.get('Pair gewijzigd') is True:
        row['Reden'] = 'PAIR_GEWIJZIGD'
        return row
    if mark_age is None or not 0 <= mark_age <= MAX_MARK_AGE_MINUTES:
        row['Reden'] = 'POSITIEMARK_VEROUDERD'
        return row
    if mark is None or mark <= 0 or fee is None or slip is None or not (0 <= fee < 1 and 0 <= slip < 1):
        row['Reden'] = 'PAPERWAARDE_ONGELDIG'
        return row
    # Same model as app.py's local Radar B (fixed fee and slippage).
    paper_usd = qty * mark * (1 - slip) * (1 - fee)
    quote_usd = _d(sample.get('Jupiter verkoopquote $'))
    if quote_usd is None or quote_usd < 0:
        row['Reden'] = 'JUPITER_QUOTE_ONGELDIG'
        return row
    # Validate samples were calculated from the same position and stake.
    s_pnl = _d(sample.get('Jupiter P/L $'))
    if s_pnl is None or abs((quote_usd - stake) - s_pnl) > Decimal('0.02'):
        row['Reden'] = 'INCONSISTENT_QUOTE_SAMPLE'
        return row
    row.update({
        'Paper verkoopwaarde $': float(round(paper_usd, 6)),
        'Jupiter verkoopwaarde $': float(round(quote_usd, 6)),
        'Paper P/L $': float(round(paper_usd - stake, 6)),
        'Jupiter P/L $': float(round(quote_usd - stake, 6)),
        'Verschil Jupiter - paper $': float(round(quote_usd - paper_usd, 6)),
        'Jupiter / paper %': float(round(quote_usd / paper_usd * 100, 3)) if paper_usd > 0 else None,
        'Beoordeling': 'QUOTE_VEEL_LAGER' if paper_usd > 0 and quote_usd < paper_usd * Decimal('0.5') else 'QUOTE_BESCHIKBAAR',
        'Reden': 'INDICATIEF_GEEN_GEGARANDEERDE_FILL',
    })
    return row


def show_paper_exit_simulator(select):
    st.subheader('📊 V6.16 — Realistische Paper Exit Simulator (Radar B)')
    st.caption('Alleen-lezen vergelijking van de originele paper-uitstapformule met '
               'de recente Jupiter-verkoopquote uit V6.14. Geen verkoop, '
               'geen historische backtest en geen wijziging van SQLite.')
    saved = st.session_state.get('v614_result') or {}
    now = datetime.now(timezone.utc)
    samples = {x['Trade-ID']: x for x in saved.get('result', [])
               if isinstance(x, dict) and x.get('Trade-ID') is not None}
    try:
        trades = select('''SELECT id,address,name,pair_address,amount_usd,quantity,
                      fee_rate,slippage_rate,last_mark,last_mark_at,exit_reason
                      FROM paper_v542_b WHERE status='OPEN' ORDER BY opened_at,id''')
    except Exception as exc:
        st.error('Kan bestaande B-posities niet lezen: ' + type(exc).__name__)
        return
    if not trades:
        st.info('Geen open lokale Radar B-posities om te vergelijken.')
        return
    rows = [evaluate_position(t, samples.get(t[0]), saved.get('checked_utc'), now)
            for t in trades]
    df = pd.DataFrame(rows)
    valid = df[df['Jupiter verkoopwaarde $'].notna()]
    unavailable = len(df) - len(valid)
    c1,c2,c3 = st.columns(3)
    c1.metric('Open B-posities', len(df))
    c2.metric('Vergelijkbare actuele quotes', len(valid))
    c3.metric('Niet vergelijkbaar', unavailable)
    if not valid.empty:
        paper = valid['Paper verkoopwaarde $'].sum()
        quoted = valid['Jupiter verkoopwaarde $'].sum()
        a,b = st.columns(2)
        a.metric('Theoretische paperwaarde (vergelijkbare posities)', f'${paper:,.4f}')
        b.metric('Indicatieve Jupiter-waarde (dezelfde posities)', f'${quoted:,.4f}',
                 delta=f'${(quoted-paper):+,.4f} vs paper')
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button('📥 V6.16 vergelijking CSV', df.to_csv(index=False).encode('utf-8'),
                       file_name='v616_paper_exit_comparison.csv', mime='text/csv',
                       key='v616_exit_comparison_csv')
    if not saved:
        st.warning('Geen V6.14-quotes in deze sessie. Klik eerst op de quotecontrole in V6.14.')
    elif unavailable:
        st.info('Niet vergelijkbaar betekent ontbrekende of verouderde quote/mark of '
                'ongeldige positiegegevens. Er wordt geen fictieve verkoopwaarde ingevuld.')
    st.caption('Let op: quotes kunnen vervallen, zijn geen gegarandeerde fills en '
               'houden niet noodzakelijk rekening met netwerk- en prioriteitskosten. '
               'Paper-uitstapwaarden gebruiken de huidige laatst opgeslagen mark, '
               'niet een historisch gesimuleerd verkoopmoment.')
