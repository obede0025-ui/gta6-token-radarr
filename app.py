            [addresses, {'encoding': 'jsonParsed', 'commitment': 'confirmed'}]) if addresses else {'value': []}
    parsed = (account_data or {}).get('value') or []
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    output = []
    for index, item in enumerate(largest_accounts):
        value = (parsed[index] or {}) if index < len(parsed) else {}
        data = value.get('data') or {}
        details = ((data.get('parsed') or {}).get('info') or {}) if isinstance(data, dict) else {}
        amount = int(item['amount'])
        decimals = int(item['decimals'])
        output.append({
            'token_account': item['address'],
            'owner': details.get('owner'),
            'raw_amount': str(amount),
            'decimals': decimals,
            'quantity': amount / 10**decimals,
            'supply_share_pct': 100 * amount / raw_supply
        })
    if not output:
        raise RuntimeError('Geen tokenaccounts gevonden')
    return {'schema_version': 1, 'mint': mint, 'checked_at': stamp,
            'accounts': output}


def _v581_store_local(snapshot):
    _v58_setup()
    rows = [(
        snapshot['mint'], snapshot['checked_at'], a['token_account'],
        a['owner'], a['raw_amount'], a['decimals'],
        a['quantity'], a['supply_share_pct']
    ) for a in snapshot['accounts']]
    with db() as conn:
        conn.executemany('''INSERT OR IGNORE INTO v58_holders
            (mint,checked_at,token_account,owner,raw_amount,decimals,quantity,supply_share_pct)
            VALUES(?,?,?,?,?,?,?,?)''', rows)


def _v581_diff(current, previous):
    if not previous or previous.get('mint') != current['mint']:
        return []
    old = {x['token_account']: x for x in previous.get('accounts', [])}
    new_ids = {x['token_account'] for x in current['accounts']}
    changes = []
    for item in current['accounts']:
        prior = old.get(item['token_account'])
        if prior is None:
            changes.append({'Tokenaccount': item['token_account'], 'Eigenaar': item['owner'],
                            'Signaal': 'Nieuw in top 20', 'Verschil tokens': None})
            continue
        raw_diff = int(item['raw_amount']) - int(prior['raw_amount'])
        if raw_diff:
            changes.append({'Tokenaccount': item['token_account'], 'Eigenaar': item['owner'],
                'Signaal': 'Toename' if raw_diff > 0 else 'Afname',
                'Verschil tokens': raw_diff / (10**int(item['decimals']))})
    for account in old:
        if account not in new_ids:
            changes.append({'Tokenaccount': account, 'Eigenaar': old[account].get('owner'),
                            'Signaal': 'Verdwenen uit top 20', 'Verschil tokens': None})
    return changes


def smart_money_panel_v581():
    _v58_setup()
    st.divider()
    st.subheader('🔎 V5.8.1 — Solana walletanalyse')
    st.caption('Onderzoek, geen tradingadvies: de grootste tokenaccounts kunnen liquiditeitspools of exchanges zijn. '
               'Een balansverschil is niet automatisch een aankoop of verkoop.')
    mint = st.text_input('Solana-token mintadres',
        value='D7eCYXewMoJ94xiXtbsiGu6XjyBEimwSpVyxYodnpump',
        key='v581_mint').strip()
    if not ADDRESS.fullmatch(mint):
        st.warning('Voer een geldig Solana-mintadres in')
        return
    cfg = _storage_config()
    if cfg is None:
        st.error('Supabase Secrets ontbreken; geen walletopslag mogelijk')
        return
    latest_name = 'radar/wallets/' + mint + '/latest.json'
    version_prefix = 'radar/wallets/' + mint + '/versions/'
    try:
        previous = _v581_remote(cfg, latest_name)
    except (requests.RequestException, ValueError, UnicodeError, KeyError) as exc:
        st.error('Vorige walletmeting niet leesbaar: ' + str(exc))
        return
    if previous:
        st.caption('Vorige veilige meting: ' + previous.get('checked_at', 'onbekend'))
    current, compare_to = previous, None
    if st.button('📡 Nieuwe walletmeting + aparte back-up', key='v581_run'):
        try:
            with st.spinner('Solana tokenaccounts controleren...'):
                fresh = _v581_fetch(mint)
                # Historische snapshot eerst apart bewaren: geen overschrijven.
                version = (version_prefix
                           + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                           + '_' + uuid4().hex[:10] + '.json')
                blob = json.dumps(fresh, ensure_ascii=False).encode('utf-8')
                _upload_object(cfg, version, blob, overwrite=False)
                # Weigeren om een nieuwere remote snapshot door een oudere te vervangen.
                latest_remote = _v581_remote(cfg, latest_name)
                if latest_remote and latest_remote.get('checked_at', '') > fresh['checked_at']:
                    raise RuntimeError('Supabase heeft inmiddels nieuwere walletgegevens')
                _upload_object(cfg, latest_name, blob, overwrite=True)
                _v581_store_local(fresh)
            st.success(f"{len(fresh['accounts'])} accounts opgeslagen in aparte Supabase walletback-up")
            st.caption('Versie: ' + version)
            current, compare_to = fresh, previous
        except (requests.RequestException, RuntimeError, sqlite3.Error, ValueError,
                KeyError, TypeError, OSError, OverflowError) as exc:
            st.error('Walletscan/back-up mislukt: ' + str(exc))
            st.caption('Als er wel een versiebestand is maar geen latest.json, blijft het versiebestand behouden.')
            return
    if not current:
        st.info('Nog geen aparte walletback-up. Start de eerste meting.')
        return
    accounts = current.get('accounts') or []
    if not accounts:
        st.warning('Wallet-back-up bevat geen accounts')
        return
    frame = pd.DataFrame(accounts)
    st.metric('Aandeel 20 grootste tokenaccounts',
              f"{sum(float(x['supply_share_pct']) for x in accounts):.2f}%")
    st.dataframe(frame, hide_index=True, width='stretch')
    changes = _v581_diff(current, compare_to)
    if changes:
        st.subheader('Veranderingen ten opzichte van vorige veilige meting')
        st.dataframe(pd.DataFrame(changes), hide_index=True, width='stretch')
    elif compare_to:
        st.caption('Geen veranderingen tussen overlappende tokenaccounts gemeten.')
    else:
        st.caption('Voor een vergelijking is een tweede meting nodig.')
    st.download_button('📥 Walletgegevens CSV', frame.to_csv(index=False).encode('utf-8'),
                       'v581_wallets.csv', 'text/csv', key='v581_download')


smart_money_panel_v581()

# ============================================================
# V5.8.2 — WALLET INTELLIGENCE
# Toevoegen onderaan de bestaande app.py
# ============================================================

_SYSTEM_PROGRAM_V582 = '11111111111111111111111111111111'


def _v582_latest_pair(mint):
    with db() as conn:
        stamps = [
            x[0] for x in conn.execute(
                'SELECT DISTINCT checked_at FROM v58_holders '
                'WHERE mint=? ORDER BY checked_at DESC LIMIT 2',
                (mint,)
            )
        ]

        data = []

        for stamp in stamps:
            rows = conn.execute(
                'SELECT token_account,owner,raw_amount,'
                'decimals,supply_share_pct '
