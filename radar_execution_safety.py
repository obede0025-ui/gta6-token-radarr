"""GTA6 Execution Safety V1 — strictly read-only, fail closed.

Reads quote monitor and healthcheck reports. Never submits transactions,
uses private keys, or edits positions. Not a token security auditor.
"""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal
MAX_REPORT_AGE_MIN = D('12')
MAX_ROUNDTRIP_LOSS_PCT = D('3.0')
MIN_MARKET_LIQUIDITY_USD = D('20000')
MAX_POSITION_PRICE_AGE_MIN = D('12')
MAX_STAKE_USDC = D('25')


def number(value):
    try:
        result = D(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def age_minutes(value, now):
    dt = timestamp(value)
    if dt is None:
        return None
    return D(str((now - dt).total_seconds())) / D('60')


def evaluate(quote_report, health_report, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now must be timezone aware')
    now = now.astimezone(timezone.utc)
    quote_age = age_minutes(quote_report.get('checked_utc'), now)
    health_age = age_minutes(health_report.get('checked_at_utc'), now)
    stake = number(quote_report.get('stake_usdc'))
    overall_checks = []
    if quote_report.get('status') != 'OK':
        overall_checks.append('QUOTE_REPORT_NOT_OK')
    if quote_age is None or quote_age < 0 or quote_age > MAX_REPORT_AGE_MIN:
        overall_checks.append('QUOTE_REPORT_STALE_OR_INVALID')
    if health_report.get('status') not in ('OK', 'AANDACHT'):
        overall_checks.append('HEALTH_REPORT_INVALID')
    if health_age is None or health_age < 0 or health_age > MAX_REPORT_AGE_MIN:
        overall_checks.append('HEALTH_REPORT_STALE_OR_INVALID')
    if stake is None or stake <= 0 or stake > MAX_STAKE_USDC:
        overall_checks.append('STAKE_OUTSIDE_PAPER_LIMIT')
    if not isinstance(quote_report.get('positions'), list):
        overall_checks.append('QUOTE_POSITIONS_INVALID')
    if not isinstance(health_report.get('open_positions'), list):
        overall_checks.append('HEALTH_POSITIONS_INVALID')
    health_by_mint = {}
    for position in (health_report.get('open_positions') or []):
        if isinstance(position, dict) and position.get('token'):
            health_by_mint.setdefault(position['token'], []).append(position)
    checked = []
    for token in quote_report.get('positions', []) if isinstance(quote_report.get('positions'), list) else []:
        if not isinstance(token, dict):
            continue
        mint = str(token.get('mint') or '')
        reasons = list(overall_checks)
        buy = token.get('buy_quote')
        sell = token.get('sell_back_quote')
        if not isinstance(buy, dict) or not isinstance(sell, dict):
            reasons.append('BUY_OR_SELL_QUOTE_MISSING')
        else:
            if buy.get('quote_only') is not True or sell.get('quote_only') is not True:
                reasons.append('QUOTE_ONLY_NOT_CONFIRMED')
            if number(buy.get('output_raw')) is None or number(sell.get('output_raw')) is None:
                reasons.append('QUOTE_AMOUNT_INVALID')
            elif number(buy.get('output_raw')) <= 0 or number(sell.get('output_raw')) <= 0:
                reasons.append('QUOTE_AMOUNT_INVALID')
            if buy.get('error_code') or sell.get('error_code'):
                reasons.append('QUOTE_ROUTE_ERROR')
        pct = number(token.get('roundtrip_pct'))
        if pct is None:
            reasons.append('ROUNDTRIP_UNKNOWN')
        elif pct < -MAX_ROUNDTRIP_LOSS_PCT:
            reasons.append('ROUNDTRIP_COST_TOO_HIGH')
        refs = health_by_mint.get(mint, [])
        if not refs:
            reasons.append('NO_MATCHING_HEALTH_POSITION')
        else:
            for p in refs:
                liq = number(p.get('market_liquidity_usd'))
                lag = number(p.get('last_market_age_minutes'))
                if liq is None or liq < MIN_MARKET_LIQUIDITY_USD:
                    reasons.append('LIQUIDITY_TOO_LOW_OR_UNKNOWN')
                if lag is None or lag < 0 or lag > MAX_POSITION_PRICE_AGE_MIN:
                    reasons.append('MARKET_DATA_STALE_OR_UNKNOWN')
        checked.append({
            'mint': mint,
            'strategies': token.get('strategies', []),
            'roundtrip_pct': token.get('roundtrip_pct'),
            'liquidity_usd': str(min(number(p.get('market_liquidity_usd')) or D(0) for p in refs)) if refs else None,
            'quote_gate': 'PASS' if not reasons else 'BLOCK',
            'quote_gate_reasons': sorted(set(reasons)),
            'live_trade_approval': 'BLOCK',
            'live_trade_reasons': ['TOKEN_CONTRACT_AND_HOLDER_RISK_NOT_VERIFIED', 'REAL_TRANSACTION_EXECUTION_NOT_VALIDATED'],
        })
    return {
        'version': '1.0',
        'mode': 'PAPER_ONLY_READ_ONLY',
        'checked_utc': now.isoformat(timespec='seconds'),
        'thresholds': {
            'maximum_roundtrip_loss_pct': str(MAX_ROUNDTRIP_LOSS_PCT),
            'minimum_market_liquidity_usd': str(MIN_MARKET_LIQUIDITY_USD),
            'max_report_age_minutes': str(MAX_REPORT_AGE_MIN),
            'max_stake_usdc': str(MAX_STAKE_USDC),
        },
        'report_integrity_issues': sorted(set(overall_checks)),
        'tokens': checked,
        'quote_gate_passed': sum(p['quote_gate'] == 'PASS' for p in checked),
        'quote_gate_blocked': sum(p['quote_gate'] == 'BLOCK' for p in checked),
        'status': 'OK' if checked and not overall_checks else 'ATTENTION',
        'all_live_trades_blocked': True,
        'notes': [
            'No private keys, swaps, transaction signing, or state changes.',
            'Quote gate evaluates CURRENT OPEN PAPER POSITIONS, not future pre-trade candidates.',
            'Jupiter quote-only amounts are indicative; fees and order fills can differ.',
            'No contract security assessment is performed: all live orders BLOCKED.',
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quotes', default='data/v594/jupiter_quote_monitor.json')
    parser.add_argument('--health', default='data/v594/radar_healthcheck.json')
    args = parser.parse_args()
    try:
        q = json.loads(Path(args.quotes).read_text(encoding='utf-8'))
        h = json.loads(Path(args.health).read_text(encoding='utf-8'))
        result = evaluate(q, h)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result = {
            'version': '1.0', 'mode': 'PAPER_ONLY_READ_ONLY',
            'checked_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'status': 'ERROR', 'tokens': [], 'quote_gate_passed': 0,
            'quote_gate_blocked': 0, 'all_live_trades_blocked': True,
            'error': type(exc).__name__,
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
