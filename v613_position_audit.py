"""V6.13 Radar B open-position audit (READ ONLY).

Uses the existing Streamlit app `select(sql, args=())` database reader.
No schema modifications, writes, network requests, or simulated exits.
"""
from datetime import datetime, timezone
import math
import pandas as pd
import streamlit as st


def _dt(value):
    try:
        if not value:
            return None
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (result.replace(tzinfo=timezone.utc) if result.tzinfo is None
                else result.astimezone(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


def _num(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _age_minutes(value, now):
    dt = _dt(value)
    return round((now-dt).total_seconds()/60, 1) if dt else None


def _grade(last_seen_min, blocked, liquidity, min_liq):
    if last_seen_min is None or last_seen_min > 12 or last_seen_min < 0:
        return 'GEEN RECENTE METING'
    if blocked or (liquidity is not None and liquidity < min_liq):
        return 'LIQUIDITEITSRISICO'
    return 'METING RECENT'


def show_position_audit(select):
    """Show operational risk only; requires existing authenticated app `select`."""
    st.subheader('🔎 V6.13 — Audit open posities Radar B')
    st.caption('Alleen-lezen: gebruikt uitsluitend bestaande SQLite-gegevens. '
               'Een marktprijs is geen gegarandeerde verkoopprijs. '
               'Dit is de lokale Streamlit Radar B, niet GitHub B2.')

    # Explicit column projection keeps this compatible with older migrated DBs.
    try:
        trades = select('''SELECT id,address,name,pair_address,opened_at,
                   entry_market,amount_usd,quantity,status,exit_reason,
                   last_mark,last_mark_at
                   FROM paper_v542_b WHERE status='OPEN'
                   ORDER BY opened_at''')
    except Exception as exc:
        st.error('Open-posities konden niet veilig worden gelezen: ' + type(exc).__name__)
        return
    if not trades:
        st.success('Momenteel geen open Radar B-paperposities.')
        return

    now = datetime.now(timezone.utc)
    min_liq = st.number_input('Auditgrens liquiditeit ($) — enkel voor weergave',
                              min_value=0.0, value=10000.0, step=1000.0,
                              help='Wijzigt geen instellingen of trades. Standaard $10.000; controleer de actieve strategieconfiguratie.')
    results=[]
    for (tid, mint, name, pair, opened, entry, stake, qty, status,
         exit_reason, last_mark, last_mark_at) in trades:
        # Latest actual observed record plus timestamp, with a stable tie-break.
        try:
            latest = select('''SELECT scanned_at,price,liquidity,volume_m5,pair_address
                FROM radar_v54 WHERE address=?
                ORDER BY scanned_at DESC, pair_address DESC LIMIT 1''', (mint,))
            # Existing decisions are informational; do not infer that they executed.
            decisions = select('''SELECT scan_at,outcome,reason,liquidity
                FROM v542_decisions WHERE address=? AND strategy='B'
                ORDER BY scan_at DESC LIMIT 1''', (mint,))
        except Exception as exc:
            st.error('Historiek kon niet worden gelezen: ' + type(exc).__name__)
            return
        scanned, price, liq, vol, observed_pair = latest[0] if latest else (None, None, None, None, None)
        marked = _num(last_mark)
        entry_price = _num(entry)
        current_price = _num(price)
        market_liq = _num(liq)
        age = _age_minutes(scanned, now)
        mark_age = _age_minutes(last_mark_at, now)
        blocked = str(exit_reason or '') == 'EXIT_BLOCKED_LIQUIDITY'
        pair_mismatch = bool(pair and observed_pair and pair != observed_pair)
        pct = (100*(current_price/entry_price-1) if entry_price and current_price and entry_price > 0 else None)
        decision = decisions[0] if decisions else (None,None,None,None)
        results.append({
            'Token':name or 'Onbekend', 'Mint':mint, 'Trade-ID':tid,
            'Status audit':_grade(age,blocked,market_liq,min_liq),
            'Exitreden':exit_reason or '—',
            'Liquiditeit $':round(market_liq,2) if market_liq is not None else None,
            'Onder auditgrens':market_liq is not None and market_liq < min_liq,
            'Laatste marktmeting UTC':scanned,
            'Marktleeftijd min':age, 'Laatste positiemark UTC':last_mark_at,
            'Markleeftijd min':mark_age,
            'Instapkoers':entry_price, 'Laatste marktkoers':current_price,
            'Koersverandering %':round(pct,2) if pct is not None else None,
            'Pair gewijzigd':pair_mismatch,
            'Laatste B-beslissing UTC':decision[0],
            'B-beslissing':decision[1], 'B-redencode':decision[2],
        })
    df = pd.DataFrame(results)
    blocked_count = sum(r['Exitreden']=='EXIT_BLOCKED_LIQUIDITY' for r in results)
    stale_count = sum(r['Status audit']=='GEEN RECENTE METING' for r in results)
    c1,c2,c3 = st.columns(3)
    c1.metric('Open Radar B-posities',len(df))
    c2.metric('Liquiditeit-geblokkeerd',blocked_count)
    c3.metric('Geen recente koersmeting',stale_count)
    st.dataframe(df,hide_index=True,use_container_width=True)
    st.download_button('📥 Audit CSV downloaden',df.to_csv(index=False).encode('utf-8'),
                       file_name='v613_position_audit.csv',mime='text/csv',
                       key='v613_position_audit_export')
    st.info('EXIT_BLOCKED_LIQUIDITY is een bewaarde status, geen bewijs dat '
            'een nieuwe quote nog steeds onmogelijk is. Bij een gewijzigde pair '
            'of verouderde marktmeting kan deze tabel geen actuele uitvoerbaarheid bevestigen.')
