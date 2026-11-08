"""V6.15 local Radar B exit-risk dashboard. READ ONLY.

Reuses the existing V6.14 on-demand Jupiter results from Streamlit session
state. It never calls Jupiter, changes trading rules, or writes to SQLite.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import pandas as pd
import streamlit as st


def num(x):
    try:
        d = Decimal(str(x))
        return d if d.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def utc(x):
    try:
        d = datetime.fromisoformat(str(x).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def assess(stake, entry, mark, liquidity, quote, quote_age_min, pair_changed):
    """Return descriptive risk signals, never a trading instruction."""
    reasons = []
    if liquidity is None:
        reasons.append('LIQUIDITEIT_ONBEKEND')
    elif liquidity < 100:
        reasons.append('KRITIEKE_LIQUIDITEIT_ONDER_100')
    elif liquidity < 10000:
        reasons.append('LIQUIDITEIT_ONDER_10000')
    if pair_changed:
        reasons.append('PAIR_GEWIJZIGD')
    if mark is None or mark <= 0 or entry is None or entry <= 0:
        reasons.append('MARKTPRIJS_ONBEKEND')
    elif mark / entry - 1 <= Decimal('-0.15'):
        reasons.append('STOP_LOSS_OVERSCHREDEN_OP_LAATSTE_MARKTPRIJS')
    if quote is None:
        reasons.append('JUPITER_QUOTE_NIET_BESCHIKBAAR')
    if quote_age_min is None or quote_age_min < 0 or quote_age_min > 12:
        reasons.append('QUOTE_VEROUDERD_OF_ONBEKEND')
    if stake is not None and stake > 0 and quote is not None and quote < stake * Decimal('0.05'):
        reasons.append('QUOTE_MINDER_DAN_5PCT_INZET')
    return ('KRITIEK' if any(x in reasons for x in (
        'KRITIEKE_LIQUIDITEIT_ONDER_100', 'QUOTE_MINDER_DAN_5PCT_INZET'))
        else 'HOOG' if reasons else 'OBSERVATIE'), reasons


def show_exit_risk(select):
    st.subheader('🚨 V6.15 — Exit Risk Management (Radar B)')
    st.caption('Alleen-lezen. Hergebruikt V6.14-quotes; geen extra API-aanvragen, '
               'geen trades en geen databasewijzigingen. Risicoscores zijn diagnose, geen koop- of verkoopadvies.')
    try:
        positions = select('''SELECT id,address,name,opened_at,entry_market,
            amount_usd,exit_reason,last_mark,last_mark_at
            FROM paper_v542_b WHERE status='OPEN' ORDER BY opened_at,id''')
    except Exception as exc:
        st.error('Risicogegevens niet beschikbaar: ' + type(exc).__name__)
        return
    if not positions:
        st.success('Geen open lokale Radar B-posities.')
        return
    saved = st.session_state.get('v614_result') or {}
    checked = utc(saved.get('checked_utc'))
    now = datetime.now(timezone.utc)
    quote_age = round((now - checked).total_seconds() / 60, 2) if checked else None
    # Old Streamlit quote samples must never be presented as current.
    samples = {r.get('Trade-ID'): r for r in saved.get('result', [])
               if isinstance(r, dict) and r.get('Trade-ID') is not None}
    output = []
    for tid, mint, name, opened, entry, stake, reason, mark, mark_at in positions:
        row = samples.get(tid, {})
        same = row.get('Mint') == mint
        quote = num(row.get('Jupiter verkoopquote $')) if same and row.get('Quotecontrole') == 'QUOTE_BESCHIKBAAR' else None
        quote_valid = same and quote is not None and quote_age is not None and 0 <= quote_age <= 12
        latest = select('''SELECT scanned_at,price,liquidity,pair_address FROM radar_v54
            WHERE address=? ORDER BY scanned_at DESC,pair_address DESC LIMIT 1''', (mint,))
        stamp, price, liq, obs_pair = latest[0] if latest else (None, None, None, None)
        pair_rows = select('SELECT pair_address FROM paper_v542_b WHERE id=?', (tid,))
        entry_pair = pair_rows[0][0] if pair_rows else None
        pair_changed = bool(entry_pair and obs_pair and entry_pair != obs_pair)
        price, liq, entry, stake = map(num, (price, liq, entry, stake))
        scan_at = utc(stamp)
        scan_age = round((now - scan_at).total_seconds() / 60, 1) if scan_at else None
        opened_dt = utc(opened)
        open_hours = round((now - opened_dt).total_seconds() / 3600, 1) if opened_dt else None
        mark_change = float(round((price / entry - 1) * 100, 2)) if price is not None and entry is not None and entry > 0 else None
        level, reasons = assess(stake, entry, price, liq, quote if quote_valid else None,
                                quote_age if quote_valid else None, pair_changed)
        if scan_age is None or scan_age < 0 or scan_age > 12:
            reasons.append('MARKTMETING_VEROUDERD')
        output.append({
            'Trade-ID': tid, 'Token': name, 'Mint': mint,
            'Risico': level, 'Redencodes': ', '.join(reasons),
            'Exitstatus DB': reason or '—',
            'Liquiditeit $': float(liq) if liq is not None else None,
            'Koersverandering %': mark_change, 'Marktleeftijd min': scan_age,
            'Open sinds uren': open_hours,
            'Jupiter quote $': float(quote) if quote_valid else None,
            'Quoteleeftijd min': quote_age if quote_valid else None,
            'Jupiter P/L $': float(quote - stake) if quote_valid and stake is not None else None,
            'Verkooproute indicatief': 'QUOTE_BESCHIKBAAR' if quote_valid else 'NIET_ACTUEEL_OF_ONBEKEND',
        })
    frame = pd.DataFrame(output)
    a, b, c = st.columns(3)
    a.metric('Open Radar B', len(frame))
    b.metric('Kritiek risico', int((frame['Risico'] == 'KRITIEK').sum()))
    c.metric('Bewaarde liquiditeitsblokkering', int((frame['Exitstatus DB'] == 'EXIT_BLOCKED_LIQUIDITY').sum()))
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.download_button('📥 Exit Risk CSV', frame.to_csv(index=False).encode('utf-8'),
                       'v615_exit_risk.csv', 'text/csv', key='v615_download')
    st.info('De exacte duur van EXIT_BLOCKED_LIQUIDITY is niet te bepalen: de eerste '
            'blokkeertijd wordt niet opgeslagen. “Open sinds uren” is de leeftijd van '
            'de positie, NIET de duur van de blokkering. Een Jupiter-quote is geen uitgevoerde verkoop.')
    if quote_age is None or quote_age > 12:
        st.warning('Geen recente V6.14-quotemeting: klik eerst in V6.14 op '
                   '“Controleer actuele Jupiter-verkoopquotes”.')
