"""Append-only bounded summary of shadow pretrade checks, no trade state changes."""
import json
from datetime import datetime, timezone
from pathlib import Path

SRC = Path('data/v594/pretrade_safety.json')
DST = Path('data/v594/pretrade_history.json')


def main():
    incoming = json.loads(SRC.read_text(encoding='utf-8'))
    if incoming.get('mode') != 'SHADOW_PRETRADE_READ_ONLY':
        raise ValueError('Unexpected source mode: history left unchanged')
    stamp = incoming.get('checked_utc')
    datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    old = json.loads(DST.read_text(encoding='utf-8')) if DST.exists() else {'version': 1, 'runs': []}
    if old.get('version') != 1 or not isinstance(old.get('runs'), list):
        raise ValueError('Unexpected history schema: history left unchanged')
    row = {'checked_utc': stamp, 'status': incoming.get('status'),
           'candidate_count':incoming.get('candidate_count', 0),
           'quote_gate_passed':incoming.get('quote_gate_passed', 0),
           'quote_gate_blocked':incoming.get('quote_gate_blocked', 0)}
    # Retain recent details, never store private credentials or raw order information.
    row['candidates'] = [{'mint': c.get('mint'), 'strategy':c.get('strategy'),
                          'quote_gate':c.get('quote_gate'), 'reasons':c.get('reasons', []),
                          'roundtrip_pct':c.get('roundtrip_pct')}
                         for c in incoming.get('candidates', []) if isinstance(c, dict)]
    rows = [r for r in old['runs'] if r.get('checked_utc') != stamp]
    rows.append(row)
    rows = sorted(rows, key=lambda x:x.get('checked_utc',''))[-2016:]
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps({'version':1,'updated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                               'runs':rows},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'OK','runs_retained':len(rows), 'candidate_count_latest':row['candidate_count']}))

if __name__ == '__main__':
    main()
