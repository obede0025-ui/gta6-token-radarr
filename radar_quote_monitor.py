"""GTA6 Radar Quote Monitor V1 — Jupiter Swap V2, strictly read-only.

Reads data/v591/portfolio.json (GitHub A/B/C portfolio). Never changes portfolio,
never signs/sends transactions, never loads private keys. Uses Jupiter /order
without taker to get quote-only price information. Reports executable-looking
route indications, NOT actual execution or guaranteed fill.

Environment: JUPITER_API_KEY required, SOLANA_RPC_URL optional.
Exit status 0 for not configured, failed quotes, or partial data: the scanning
pipeline should not be blocked by an optional analysis report.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

import requests

JUP_ORDER = 'https://api.jup.ag/swap/v2/order'
SOLANA_RPC = 'https://api.mainnet-beta.solana.com'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
USDC_DECIMALS = 6
MINT_PATTERN = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
D = Decimal


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def positive_raw(value):
    """Validate atomic input / output as a non-negative base-10 integer."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Invalid atomic amount type')
    txt = str(value)
    if not txt.isdigit() or len(txt) > 40:
        raise ValueError('Invalid atomic amount')
    return int(txt)


def token_amount_to_raw(amount, decimals):
    if isinstance(amount, bool) or not 0 <= decimals <= 18:
        raise ValueError('Invalid decimals')
    value = D(str(amount))
    if not value.is_finite() or value <= 0:
        raise ValueError('Invalid token amount')
    atomic = (value * (D(10) ** decimals)).to_integral_value(rounding=ROUND_DOWN)
    if atomic <= 0:
        raise ValueError('Amount too small for token decimals')
    return int(atomic)


def quote(session, key, input_mint, output_mint, raw_amount):
    if not MINT_PATTERN.fullmatch(input_mint) or not MINT_PATTERN.fullmatch(output_mint):
        raise ValueError('Invalid token mint')
    raw_amount = positive_raw(raw_amount)
    if raw_amount <= 0:
        raise ValueError('Zero amount')
    response = session.get(
        JUP_ORDER,
        params={
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': str(raw_amount),
            # No 'taker': QUOTE ONLY, cannot build signable transaction.
        },
        headers={'x-api-key': key, 'Accept': 'application/json'},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('Jupiter returned invalid payload')
    out = positive_raw(payload.get('outAmount'))
    if not out:
        raise ValueError('Jupiter returned empty output amount')
    # No raw response attached: avoid persisting transaction/order metadata.
    return {
        'input_raw': raw_amount,
        'output_raw': out,
        'router': payload.get('router'),
        'mode': payload.get('mode'),
        'fee_bps': payload.get('feeBps'),
        'fee_mint': payload.get('feeMint'),
        'error_code': payload.get('errorCode'),
        'quote_only': True,
    }


def get_decimals(session, mint, rpc_url):
    response = session.post(
        rpc_url,
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'getTokenSupply', 'params': [mint]},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or 'error' in payload:
        raise ValueError('RPC token supply unavailable')
    decimals = payload['result']['value']['decimals']
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise ValueError('RPC returned invalid decimals')
    return decimals


def sanitize_error(exc):
    """No response body, API key, or configured RPC URL in the report."""
    if isinstance(exc, requests.Timeout):
        return 'TIMEOUT'
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 'UNKNOWN'
        return f'HTTP_{status}'
    if isinstance(exc, requests.RequestException):
        return 'NETWORK_ERROR'
    if isinstance(exc, (ValueError, KeyError, InvalidOperation, TypeError)):
        return 'INVALID_DATA_OR_ROUTE'
    return 'QUOTE_ERROR'


def compare_positions(state, *, session, api_key, rpc_url, stake_usdc):
    stake_atomic = token_amount_to_raw(stake_usdc, USDC_DECIMALS)
    positions = []
    # Mint is shared across strategies; ask one roundtrip per mint.
    grouped = {}
    for strategy in ('A', 'B', 'C'):
        for mint, item in (state['open'].get(strategy) or {}).items():
            if MINT_PATTERN.fullmatch(mint) and isinstance(item, dict):
                grouped.setdefault(mint, []).append((strategy, item))

    for mint, grouped_positions in grouped.items():
        result = {'mint': mint, 'strategies': [s for s, _ in grouped_positions],
                  'buy_quote': None, 'sell_back_quote': None,
                  'roundtrip_usdc': None, 'roundtrip_pct': None,
                  'positions': [], 'warnings': []}
        try:
            buy = quote(session, api_key, USDC, mint, stake_atomic)
            result['buy_quote'] = buy
            sell = quote(session, api_key, mint, USDC, buy['output_raw'])
            result['sell_back_quote'] = sell
            usd_out = D(sell['output_raw']) / D(10 ** USDC_DECIMALS)
            result['roundtrip_usdc'] = str(usd_out)
            result['roundtrip_pct'] = str(round((usd_out / stake_usdc - 1) * 100, 3))
            result['warnings'].append('Indicative roundtrip only; no execution, possible spread/price impact')
        except Exception as exc:
            result['warnings'].append('ROUNDTRIP_' + sanitize_error(exc))

        # Do not use buy quote to imply a market sell of the historical position.
        # A separate quote from the stored paper position quantity is required.
        try:
            decimals = get_decimals(session, mint, rpc_url)
        except Exception as exc:
            decimals = None
            result['warnings'].append('DECIMALS_' + sanitize_error(exc))

        for strategy, position in grouped_positions:
            one = {
                'strategy': strategy,
                'opened_at': position.get('opened_at'),
                'entry_market': position.get('entry_market'),
                'position_quantity': position.get('quantity'),
                'estimated_exit_usdc': None,
                'estimated_net_pnl_usdc_before_network_cost': None,
                'exit_router': None, 'status': 'UNAVAILABLE',
            }
            if decimals is not None:
                try:
                    raw_qty = token_amount_to_raw(position['quantity'], decimals)
                    exit_quote = quote(session, api_key, mint, USDC, raw_qty)
                    exit_usdc = D(exit_quote['output_raw']) / D(10 ** USDC_DECIMALS)
                    one['estimated_exit_usdc'] = str(exit_usdc)
                    one['estimated_net_pnl_usdc_before_network_cost'] = str(
                        (exit_usdc - stake_usdc).quantize(D('0.0001')))
                    one['exit_router'] = exit_quote['router']
                    one['status'] = 'QUOTE_AVAILABLE'
                except Exception as exc:
                    one['status'] = sanitize_error(exc)
            result['positions'].append(one)
        positions.append(result)
    return positions


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='data/v591/portfolio.json')
    parser.add_argument('--stake-usdc', default='25')
    args = parser.parse_args(argv)
    stamp = iso_now()
    report = {
        'version': '1.0', 'checked_utc': stamp,
        'mode': 'JUPITER_SWAP_V2_QUOTE_ONLY', 'status': 'NOT_CONFIGURED',
        'stake_usdc': args.stake_usdc, 'positions': [],
        'notes': [
            'Read-only API calls. No wallet, no transaction signing or execution.',
            'Quotes can expire immediately; values are indicative, not fills.',
            'Roundtrip ignores network/priority/rent costs; position exit comparison also ignores those costs.',
            'Existing GitHub A/B/C paper portfolio unchanged.',
        ],
    }
    key = os.environ.get('JUPITER_API_KEY', '').strip()
    if not key:
        report['notes'].append('Add JUPITER_API_KEY as GitHub Actions secret to enable quotes.')
    else:
        try:
            state = json.loads(Path(args.state).read_text(encoding='utf-8'))
            if state.get('version') != 1 or not isinstance(state.get('open'), dict):
                raise ValueError('Unexpected portfolio state')
            stake = D(args.stake_usdc)
            if not stake.is_finite() or not D('0') < stake <= D('100'):
                raise ValueError('Invalid stake')
            rpc = os.environ.get('SOLANA_RPC_URL') or SOLANA_RPC
            if not rpc.startswith('https://'):
                raise ValueError('RPC must use HTTPS')
            with requests.Session() as session:
                report['positions'] = compare_positions(
                    state, session=session, api_key=key, rpc_url=rpc, stake_usdc=stake)
            report['status'] = 'OK' if all(
                all(pos['status'] == 'QUOTE_AVAILABLE' for pos in token['positions'])
                and token['roundtrip_usdc'] is not None
                for token in report['positions']
            ) else 'PARTIAL'
            report['tracked_tokens'] = len(report['positions'])
        except Exception as exc:
            report['status'] = 'ERROR'
            report['notes'].append(sanitize_error(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
