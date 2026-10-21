"""
Safe backup of the database (works on a live DB).
SQLite: the sqlite3 online backup, written as .sqlite3.gz. PostgreSQL: `pg_dump` (custom format), written as .pgdump.
Old backups are cleaned up automatically (keep = how many to retain).
"""
import glob
import gzip
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

NAME_RE = re.compile(r'^medsync_\d{8}_\d{6}\.(sqlite3\.gz|pgdump)$')


class BackupError(Exception):
    pass


def backup_dir():
    d = Path(getattr(settings, 'BACKUP_DIR', settings.BASE_DIR / 'backups'))
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_postgres():
    return 'postgresql' in settings.DATABASES['default']['ENGINE']


def _db_path():
    db = settings.DATABASES['default']
    if 'sqlite' not in db['ENGINE']:
        raise BackupError('This backup works only with SQLite and PostgreSQL. For another database, use its own dump tool.')
    return Path(db['NAME'])


def find_pg_dump():
    """Full path of pg_dump: the PG_DUMP setting, the PATH, or the usual install folders. '' when not found."""
    for cand in (os.environ.get('PG_DUMP', '').strip().strip('"'), shutil.which('pg_dump') or ''):
        if cand and Path(cand).is_file():
            return cand
    patterns = (r'C:\Program Files\PostgreSQL\*\bin\pg_dump.exe', '/usr/lib/postgresql/*/bin/pg_dump',
                '/usr/local/pgsql*/bin/pg_dump', '/opt/homebrew/opt/postgresql*/bin/pg_dump')
    found = sorted((p for pat in patterns for p in glob.glob(pat)),
                   key=lambda p: [int(x) for x in re.findall(r'\d+', p)], reverse=True)      # newest version first
    return found[0] if found else ''


def _backup_postgres(target):
    exe = find_pg_dump()
    if not exe:
        raise BackupError('pg_dump was not found. Install the PostgreSQL tools, or set PG_DUMP in .env to the full path of pg_dump.')
    db = settings.DATABASES['default']
    cmd = [exe, '-Fc', '-h', str(db.get('HOST') or 'localhost'), '-p', str(db.get('PORT') or '5432'),
           '-U', str(db.get('USER') or ''), '-f', str(target), str(db['NAME'])]
    env = dict(os.environ, PGPASSWORD=str(db.get('PASSWORD') or ''))          # the password is not shown in the command line
    try:
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        target.unlink(missing_ok=True)
        raise BackupError(f'pg_dump could not run: {str(e)[:150]}')
    if done.returncode != 0:
        target.unlink(missing_ok=True)
        raise BackupError(f"pg_dump failed: {(done.stderr or 'no message').strip()[:250]}")


def make_backup(keep=14):
    if is_postgres():
        target = backup_dir() / f"medsync_{datetime.now():%Y%m%d_%H%M%S}.pgdump"
        _backup_postgres(target)
        if not target.exists() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise BackupError('pg_dump produced an empty file.')
        prune(keep)
        return target
    src_path = _db_path()
    if not src_path.exists():
        raise BackupError('Database file not found.')
    name = f"medsync_{datetime.now():%Y%m%d_%H%M%S}.sqlite3.gz"
    target = backup_dir() / name
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / 'snapshot.sqlite3'
        src = sqlite3.connect(f"{src_path.resolve().as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(snap, 'rb') as f_in, gzip.open(target, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    prune(keep)
    return target


def list_backups():
    out = []
    for f in sorted(backup_dir().glob('medsync_*'), reverse=True):
        if NAME_RE.match(f.name):
            st = f.stat()
            out.append(dict(name=f.name, size_kb=max(1, st.st_size // 1024),
                            when=datetime.fromtimestamp(st.st_mtime)))
    return out


def prune(keep):
    if keep and keep > 0:
        for old in list_backups()[keep:]:
            try:
                (backup_dir() / old['name']).unlink()
            except OSError:
                pass


def backup_path(name):
    """Path of a backup created by this app only (guards against path traversal)."""
    if not NAME_RE.match(name or ''):
        return None
    p = backup_dir() / name
    return p if p.is_file() else None
