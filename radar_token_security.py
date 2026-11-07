"""GTA6 Token Security V1 - read-only Solana mint-account screening.

Source: Solana getAccountInfo(jsonParsed). Checks mint/freeze authorities,
program owner and visible Token-2022 extensions. No holder concentration, LP
lock, honeypot or swap execution verification. NEVER approves live trading.

Inputs: data/v594/pretrade_safety.json and data/v594/jupiter_quote_monitor.json.
Output: JSON on stdout. Existing portfolios and database remain untouched.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

LEGACY = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPF2uL8CaNKVhnK2tS9'
DEFAULT_RPC = 'https://api.mainnet-beta.solana.com'
MINT_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
BLOCK_EXTENSIONS = {'permanentdelegate', 'transferhook', 'nontransferable',
                    'defaultaccountstate', 'transferfeeconfig',
                    'confidentialtransfermint', 'confidentialtransferfeeconfig',
                    'pausableconfig', 'mintcloseauthority'}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_report(path):
    try:
        obj = json.loads(Path(path).read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def token_mints(pretrade, quotes, limit):
    sources = {}
    for row in pretrade.get('candidates', []) if isinstance(pretrade.get('candidates'), list) else []:
        if isinstance(row, dict):
            mint = row.get('mint')
            if isinstance(mint, str) and MINT_RE.fullmatch(mint):
                sources.setdefault(mint, set()).add('pretrade_candidate')
    for row in quotes.get('positions', []) if isinstance(quotes.get('positions'), list) else []:
        if isinstance(row, dict):
            mint = row.get('mint')
            if isinstance(mint, str) and MINT_RE.fullmatch(mint):
                sources.setdefault(mint, set()).add('open_paper_position')
    return [(mint, sorted(origins)) for mint, origins in list(sources.items())[:limit]], len(sources)


def rpc_get_account(session, url, mint):
    response = session.post(url, json={'jsonrpc': '2.0', 'id': 1,
        'method': 'getAccountInfo', 'params': [mint, {'encoding': 'jsonParsed', 'commitment': 'confirmed'}]},
        timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('RPC_ERROR')
    result = payload.get('result')
    if not isinstance(result, dict):
        raise ValueError('INVALID_RPC_RESULT')
    return result.get('value')


def evaluate_mint(mint, sources, account):
    reasons, warnings = [], []
    info = {'mint':mint, 'sources':sources, 'program':'UNKNOWN',
            'mint_authority': 'UNKNOWN', 'freeze_authority':'UNKNOWN',
            'extensions':[], 'security_gate':'BLOCK',
            'live_trade_approval':'BLOCK', 'reasons':reasons, 'warnings':warnings}
    if not isinstance(account, dict):
        reasons.append('MINT_ACCOUNT_NOT_FOUND')
        return info
    owner = account.get('owner')
    info['program'] = 'SPL_TOKEN' if owner == LEGACY else ('TOKEN_2022' if owner == TOKEN_2022 else 'UNKNOWN')
    if owner not in (LEGACY, TOKEN_2022):
        reasons.append('UNKNOWN_PROGRAM_OWNER')
        return info
    data = account.get('data')
    parsed = data.get('parsed') if isinstance(data, dict) else None
    if not isinstance(parsed, dict) or parsed.get('type') != 'mint':
        reasons.append('MINT_ACCOUNT_NOT_JSON_PARSED')
        return info
    details = parsed.get('info')
    if not isinstance(details, dict) or not all(k in details for k in ('mintAuthority','freezeAuthority','decimals','supply')):
        reasons.append('INCOMPLETE_MINT_FIELDS')
        return info
    mint_auth, freeze_auth = details['mintAuthority'], details['freezeAuthority']
    info['mint_authority'] = mint_auth
    info['freeze_authority'] = freeze_auth
    if mint_auth is not None:
        reasons.append('MINT_AUTHORITY_ACTIVE')
    if freeze_auth is not None:
        reasons.append('FREEZE_AUTHORITY_ACTIVE')
    ext = details.get('extensions', [])
    if owner == TOKEN_2022:
        # V1 deliberately fails closed: extensions and their effective state need a dedicated audit.
        reasons.append('TOKEN_2022_REQUIRES_EXTENSION_AUDIT')
        if not isinstance(ext, list):
            reasons.append('EXTENSIONS_UNPARSEABLE')
            ext = []
        for item in ext:
            kind = item.get('extension') if isinstance(item, dict) else None
            if isinstance(kind, str):
                info['extensions'].append(kind)
                if kind.lower().replace('_', '').replace('-', '') in BLOCK_EXTENSIONS:
                    reasons.append('RISK_EXTENSION_' + kind.upper())
            else:
                reasons.append('UNKNOWN_EXTENSION')
    # Neither authority existing only clears basic authority checks, never proves safety.
    if not reasons:
        info['security_gate'] = 'BASIC_CHECKS_CLEAR'
        warnings.append('Not a safety certification: LP lock, holder concentration, and transferability unverified')
    return info


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--pretrade', default='data/v594/pretrade_safety.json')
    parser.add_argument('--quotes', default='data/v594/jupiter_quote_monitor.json')
    parser.add_argument('--limit', type=int, default=15)
    args = parser.parse_args(argv)
    report = {'version': '1.0', 'checked_utc': now_iso(),
              'mode':'READ_ONLY_MINT_AUTHORITY_SCREENING', 'status':'ATTENTION',
              'all_live_trades_blocked':True, 'tokens':[], 'checked_count':0,
              'block_count':0, 'basic_checks_clear_count':0,
              'notes':['No holder / LP lock / transferability / honeypot verification',
                       'Token-2022 is blocked pending dedicated extension audit',
                       'No wallet/private keys, no trades, no portfolio/database changes']}
    pretrade = read_report(args.pretrade)
    quotes = read_report(args.quotes)
    selected, total = token_mints(pretrade, quotes, max(1, min(args.limit, 30)))
    report['candidate_mints_total'] = total
    if not selected:
        report['status'] = 'NO_TOKENS'
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    rpc = os.environ.get('SOLANA_RPC_URL') or DEFAULT_RPC
    url = urlparse(rpc)
    if url.scheme != 'https' or not url.netloc or url.username or url.password:
        report['status'] = 'RPC_NOT_CONFIGURED'
        report['notes'].append('SOLANA_RPC_URL must be an HTTPS RPC endpoint')
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    with requests.Session() as session:
        for mint, sources in selected:
            try:
                row = evaluate_mint(mint, sources, rpc_get_account(session, rpc, mint))
            except requests.Timeout:
                row = evaluate_mint(mint, sources, None)
                row['reasons'] = ['RPC_TIMEOUT']
            except requests.RequestException:
                row = evaluate_mint(mint, sources, None)
                row['reasons'] = ['RPC_NETWORK_OR_HTTP_ERROR']
            except (ValueError, TypeError, KeyError):
                row = evaluate_mint(mint, sources, None)
                row['reasons'] = ['INVALID_RPC_RESPONSE']
            report['tokens'].append(row)
    report['checked_count'] = len(report['tokens'])
    report['block_count'] = sum(x['security_gate'] == 'BLOCK' for x in report['tokens'])
    report['basic_checks_clear_count'] = sum(x['security_gate'] == 'BASIC_CHECKS_CLEAR' for x in report['tokens'])
    report['status'] = 'OK' if report['tokens'] and not any(
        any(r.startswith('RPC_') or r.startswith('INVALID_RPC') for r in t['reasons'])
        for t in report['tokens']) else 'ATTENTION'
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
