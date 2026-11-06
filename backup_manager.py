
"""Fail-closed versioned Supabase Storage backup for GTA6 SQLite.

Single Streamlit deployment only. No concurrent writer coordination across servers.
"""
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st

_LOCK = threading.RLock()
_LAST_SUCCESS = 0.0


def _setting(name, default=''):
    value = os.getenv(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, KeyError, AttributeError):
        return default


def _config():
    host = _setting('SUPABASE_URL').rstrip('/')
    key = _setting('SUPABASE_SECRET_KEY')
    bucket = _setting(
        'SUPABASE_BACKUP_BUCKET',
        'gta6-private-backups'
    )
    name = _setting(
        'SUPABASE_BACKUP_OBJECT',
        'radar/gta6_radar.db'
    ).lstrip('/')

    if not host or not key:
        return None

    if (
        not host.startswith('https://')
        or '/rest/v1' in host
        or not key.startswith('sb_secret_')
    ):
        raise ValueError(
            'Ongeldige Supabase Project URL / Secret Key'
        )

    return host, key, bucket, name


def _url(config, object_name):
    host, _, bucket, _ = config
    return (
        host + '/storage/v1/object/'
        + quote(bucket, safe='')
        + '/'
        + quote(object_name, safe='/')
    )


def _headers(config, upload=False):
    headers = {'apikey': config[1]}

    if upload:
        headers.update({
            'x-upsert': 'true',
            'Content-Type': 'application/octet-stream'
        })

    return headers


def _fetch(config, object_name):
    response = requests.get(
        _url(config, object_name),
        headers=_headers(config),
        timeout=40
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()
    return response.content


def _put(config, object_name, data):
    response = requests.post(
        _url(config, object_name),
        headers=_headers(config, upload=True),
        data=data,
        timeout=60
    )
    response.raise_for_status()


def _check(blob):
    with tempfile.NamedTemporaryFile(
        suffix='.db'
    ) as tmp:
        tmp.write(blob)
        tmp.flush()

        with sqlite3.connect(
            'file:' + tmp.name + '?mode=ro',
            uri=True
        ) as connection:

            result = connection.execute(
                'PRAGMA quick_check'
            ).fetchone()[0]

            if result != 'ok':
                raise sqlite3.DatabaseError(
                    'Databaseintegriteit ongeldig'
                )

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table'"
                )
            }

            required = {
                'paper_v54',
                'paper_v542_b',
                'radar_v54',
                'v542_runs'
            }

            if not required.issubset(tables):
                raise sqlite3.DatabaseError(
                    'Database mist belangrijke radartabellen'
                )

            counts = {
                table: connection.execute(
                    'SELECT COUNT(*) FROM ' + table
                ).fetchone()[0]
                for table in required
            }

    return counts


def _snapshot(db_path):
    with tempfile.NamedTemporaryFile(
        suffix='.db'
    ) as tmp:

        with sqlite3.connect(
            str(db_path),
            timeout=30
        ) as source:

            with sqlite3.connect(
                tmp.name
            ) as destination:
                source.backup(destination)

        return Path(tmp.name).read_bytes()


def restore():
    """
    Restore only when local database is missing.
    Never overwrite an existing local database.
    """
    path = Path(
        os.getenv(
            'GTA6_RADAR_DB',
            'gta6_radar.db'
        )
    )

    if path.exists():
        return 'Bestaande lokale database behouden'

    config = _config()

    if config is None:
        raise RuntimeError(
            'Geen lokale database en geen Supabase Secrets; '
            'herstel gestopt'
        )

    _, _, _, name = config
    blob = _fetch(config, name)

    if blob is None:
        raise RuntimeError(
            'Geen lokale database en geen remote back-up; '
            'herstel gestopt'
        )

    counts = _check(blob)
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix='.db',
        dir=str(path.parent)
    ) as tmp:
        tmp.write(blob)
        tempname = tmp.name

    try:
        os.replace(tempname, path)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)

    return (
        'Back-up hersteld: '
        + ', '.join(
            f'{key}={value}'
            for key, value in sorted(counts.items())
        )
    )


def backup(force=False):
    """
    Create snapshot, save an hourly version first,
    then update active backup.

    Refuse to overwrite remote backup when local
    protected table counts have decreased.
    """
    global _LAST_SUCCESS

    with _LOCK:
        config = _config()

        if config is None:
            return 'Externe back-up niet geconfigureerd'

        if (
            not force
            and time.monotonic() - _LAST_SUCCESS < 120
        ):
            return (
                'Back-up minder dan 2 minuten '
                'geleden uitgevoerd'
            )

        path = Path(
            os.getenv(
                'GTA6_RADAR_DB',
                'gta6_radar.db'
            )
        )

        if not path.exists():
            raise RuntimeError(
                'Lokale database ontbreekt: '
                'upload tegengehouden'
            )

        blob = _snapshot(path)
        current = _check(blob)

        _, _, _, name = config
        previous_blob = _fetch(config, name)

        if previous_blob is not None:
            previous = _check(previous_blob)

            for table in (
                'paper_v54',
                'paper_v542_b',
                'radar_v54',
                'v542_runs'
            ):
                if current[table] < previous[table]:
                    raise RuntimeError(
                        'VEILIGHEIDSBLOKKERING: '
                        f'{table}: '
                        f'lokaal {current[table]} '
                        f'< Supabase {previous[table]}. '
                        'Oude back-up blijft intact.'
                    )

        timestamp = datetime.now(
            timezone.utc
        ).strftime('%Y-%m-%d/%H')

        prefix = (
            name.rsplit('/', 1)[0]
            if '/' in name
            else 'radar'
        )

        version_name = (
            f'{prefix}/versions/{timestamp}.db'
        )

        # First preserve a version.
        _put(config, version_name, blob)

        # Only update latest after a successful version upload.
        _put(config, name, blob)

        _LAST_SUCCESS = time.monotonic()

        return (
            'Back-up OK · '
            + datetime.now(timezone.utc).strftime(
                '%H:%M:%S UTC'
            )
            + f' · {len(blob):,} bytes'
            + f' · versie {version_name}'
        )
