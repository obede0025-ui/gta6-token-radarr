
"""V6.14: on-demand, read-only Jupiter quote diagnostic for local Radar B blocked positions.

No database writes, portfolio mutation, transaction creation, signing or submission.
A quote is an indicative snapshot, not a guarantee of execution or a historical fill.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
import requests
import streamlit as st

from radar_quote_monitor import (
    USDC, USDC_DECIMALS, MINT_PATTERN, get_decimals, quote,
    token_amount_to_raw, sanitize_error,
)

MAX_POSITIONS = 8
FRESH_MINUTES = 12
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


def _decimal(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _secret(secret, key):
    try:
        return str(secret(key) or '').strip()
    except (KeyError, FileNotFoundError, AttributeError, TypeError, ValueError):
        return ''


def _latest_observation(select, mint):
    rows = select('''SELECT scanned_at,price,liquidity,pair_address
        FROM radar_v54 WHERE address=?
        ORDER BY scanned_at DESC,pair_address DESC LIMIT 1''', (mint,))
    return rows[0] if rows else (None, None, None, None)


def audit_position(trade, observation, api_key, rpc_url, now, session):
    tid, mint, name, pair, opened, stake, quantity, fee, slip, reason = trade
    scanned_at, last_price, liquidity, observed_pair = observation

    stamp = _timestamp(scanned_at)
    age = round((now-stamp).total_seconds()/60, 2) if stamp else None

    stake, quantity, fee, slip = map(
        _decimal, (stake, quantity, fee, slip)
    )
    last_price, liquidity = (
        _decimal(last_price), _decimal(liquidity)
    )

    result = {
        'Trade-ID': tid,
        'Token': name,
        'Mint': mint,
        'Uitstapstatus DB': reason,
        'Liquiditeit $': float(liquidity) if liquidity is not None else None,
        'Laatste meting UTC': scanned_at,
        'Marktleeftijd min': age,
        'Pair gewijzigd': bool(
            pair and observed_pair and pair != observed_pair
        ),
        'Paper uitstap $': None,
        'Jupiter verkoopquote $': None,
        'Jupiter P/L $': None,
        'Verschil quote-paper $': None,
        'Quotecontrole': 'NIET_GETEST',
        'Reden': '',
    }

    if not MINT_PATTERN.fullmatch(str(mint or '')):
        result.update(
            Quotecontrole='ONGELDIG',
            Reden='ONGELDIGE_MINT'
        )
        return result

    if stake is None or stake <= 0 or quantity is None or quantity <= 0:
        result.update(
            Quotecontrole='ONGELDIG',
            Reden='ONGELDIGE_POSITIE'
        )
        return result

    fresh = (
        age is not None
        and 0 <= age <= FRESH_MINUTES
    )

    if (
        fresh
        and last_price is not None
        and last_price > 0
        and fee is not None
        and slip is not None
        and 0 <= fee < 1
        and 0 <= slip < 1
        and not result['Pair gewijzigd']
    ):
        paper_exit = quantity * last_price * (1-slip) * (1-fee)
        result['Paper uitstap $'] = float(round(paper_exit, 6))

    if not api_key:
        result.update(
            Quotecontrole='NIET_GETEST',
            Reden='JUPITER_API_KEY_ONTBREEKT'
        )
        return result

    if not rpc_url.startswith('https://'):
        result.update(
            Quotecontrole='NIET_GETEST',
            Reden='RPC_URL_ONGELDIG'
        )
        return result

    try:
        decimals = get_decimals(session, mint, rpc_url)
        raw = token_amount_to_raw(quantity, decimals)

        response = quote(
            session, api_key, mint, USDC, raw
        )

        if response.get('error_code') or not response.get('quote_only'):
            result.update(
                Quotecontrole='GEEN_BETROUWBARE_QUOTE',
                Reden='ROUTE_ERROR'
            )
            return result

        usdc = (
            Decimal(str(response['output_raw']))
            / Decimal(10 ** USDC_DECIMALS)
        )

        result['Jupiter verkoopquote $'] = float(
            round(usdc, 6)
        )
        result['Jupiter P/L $'] = float(
            round(usdc - stake, 6)
        )

        if result['Paper uitstap $'] is not None:
            result['Verschil quote-paper $'] = float(
                round(
                    usdc - Decimal(str(result['Paper uitstap $'])),
                    6
                )
            )

        result['Quotecontrole'] = 'QUOTE_BESCHIKBAAR'
        result['Reden'] = (
            'VEROUDERDE_MARKTPRIJS' if not fresh else
            'PAIR_GEWIJZIGD' if result['Pair gewijzigd'] else
            'ACTUELE_INDICATIEVE_QUOTE'
        )

    except Exception as exc:
        result.update(
            Quotecontrole='QUOTE_ONBESCHIKBAAR',
            Reden=sanitize_error(exc)
        )

    return result


def show_blocked_exit_quotes(select, secret):
    st.subheader(
        '🔬 V6.14 — Jupiter-exitdiagnose geblokkeerde Radar B-posities'
    )

    st.caption(
        'Handmatige, alleen-lezen controle. Geen verkoop, '
        'geen databasewijzigingen. Quotes zijn momentopnames '
        'en kunnen vervallen; een lage liquiditeit blijft een risico.'
    )

    try:
        trades = select('''
            SELECT id,address,name,pair_address,opened_at,
                   amount_usd,quantity,fee_rate,slippage_rate,exit_reason
            FROM paper_v542_b
            WHERE status='OPEN'
              AND exit_reason='EXIT_BLOCKED_LIQUIDITY'
            ORDER BY opened_at,id
        ''')
    except Exception as exc:
        st.error(
            'Kan lokale Radar B-posities niet ophalen: '
            + type(exc).__name__
        )
        return

    if not trades:
        st.info(
            'Geen open B-posities met EXIT_BLOCKED_LIQUIDITY op dit moment.'
        )
        return

    st.write(
        f'Gevonden: **{len(trades)}** geblokkeerde open posities.'
    )

    if len(trades) > MAX_POSITIONS:
        st.warning(
            f'Per controle maximaal {MAX_POSITIONS} posities: '
            'API-belasting beperken.'
        )

    key = _secret(secret, 'JUPITER_API_KEY')
    rpc_url = (
        _secret(secret, 'SOLANA_RPC_URL')
        or DEFAULT_RPC
    )

    if not key:
        st.warning(
            'JUPITER_API_KEY ontbreekt in Streamlit Secrets. '
            'Een GitHub Actions-secret wordt niet automatisch '
            'gedeeld met Streamlit.'
        )

    if st.button(
        '🔎 Controleer actuele Jupiter-verkoopquotes',
        key='v614_manual_quote_check'
    ):
        now = datetime.now(timezone.utc)
        output = []

        with requests.Session() as session:
            for trade in trades[:MAX_POSITIONS]:
                try:
                    observation = _latest_observation(
                        select, trade[1]
                    )

                    output.append(
                        audit_position(
                            trade, observation, key,
                            rpc_url, now, session
                        )
                    )

                except Exception as exc:
                    output.append({
                        'Trade-ID': trade[0],
                        'Token': trade[2],
                        'Mint': trade[1],
                        'Quotecontrole': 'FOUT',
                        'Reden': type(exc).__name__,
                    })

        st.session_state['v614_result'] = {
            'checked_utc': now.isoformat(timespec='seconds'),
            'result': output,
        }

    saved = st.session_state.get('v614_result')

    if saved:
        st.caption(
            'Quotes aangevraagd op '
            + saved['checked_utc']
            + ' UTC; verversen vereist opnieuw klikken.'
        )

        frame = pd.DataFrame(saved['result'])

        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            'Download exitdiagnose CSV',
            frame.to_csv(index=False).encode(),
            file_name='v614_exitdiagnose.csv',
            mime='text/csv',
            key='v614_csv'
        )

        st.info(
            'HTTP_429 betekent API-rate limiting, niet automatisch '
            'onmogelijke verkoop. Zonder quote of bij verouderde '
            'koersmeting is een eerlijke uitvoeringsvergelijking '
            'niet mogelijk. De oorspronkelijke blokkering heeft '
            'geen opgeslagen starttijd; de exacte blokkeringduur '
            'is dus onbekend.'
        )
