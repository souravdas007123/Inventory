"""
Safe backup of the SQLite database (works on a live DB; uses the sqlite3 online backup).
Backups are written as .sqlite3.gz files. Old backups are cleaned up automatically (keep = how many to retain).
"""
import gzip
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

NAME_RE = re.compile(r'^medsync_\d{8}_\d{6}\.sqlite3\.gz$')


class BackupError(Exception):
    pass


def backup_dir():
    d = Path(getattr(settings, 'BACKUP_DIR', settings.BASE_DIR / 'backups'))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path():
    db = settings.DATABASES['default']
    if 'sqlite' not in db['ENGINE']:
        raise BackupError('This backup works only with SQLite. For another database, use its own dump tool.')
    return Path(db['NAME'])


def make_backup(keep=14):
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
    for f in sorted(backup_dir().glob('medsync_*.sqlite3.gz'), reverse=True):
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


def email_backup(path, max_mb=None):
    """Emails one backup file to settings.BACKUP_EMAIL_TO. Returns a short message; never raises.
    Nothing is sent when no address is set or the file is bigger than the limit (mail servers refuse large files)."""
    to = list(getattr(settings, 'BACKUP_EMAIL_TO', []) or [])
    if not to:
        return 'Off-server backup email is not set (DJANGO_BACKUP_EMAIL_TO).'
    limit = (getattr(settings, 'BACKUP_EMAIL_MAX_MB', 15) if max_mb is None else max_mb) * 1024 * 1024
    try:
        path = Path(path)
        if path.stat().st_size > limit:
            return f'Backup is bigger than {limit // (1024 * 1024)} MB, so it was not emailed.'
        from django.core.mail import EmailMessage
        msg = EmailMessage(f'MedSync backup {path.name}', 'The latest MedSync database backup is attached. Keep it safe: it holds all shop data.',
                           settings.DEFAULT_FROM_EMAIL, to)
        msg.attach(path.name, path.read_bytes(), 'application/gzip')
        msg.send(fail_silently=False)
    except Exception as e:
        return f'Backup email failed: {e}'
    return f'Backup emailed to {", ".join(to)}.'
