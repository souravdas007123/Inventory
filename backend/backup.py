"""
SQLite database ka safe backup (chalte hue DB par bhi theek rehta hai, sqlite3 ka online backup use hota hai).
Backup .sqlite3.gz file me banta hai. Purane backups apne aap saaf (keep = kitne rakhne hain).
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
        raise BackupError('Ye backup sirf SQLite ke liye hai. Doosre database ka apna dump tool use karein.')
    return Path(db['NAME'])


def make_backup(keep=14):
    src_path = _db_path()
    if not src_path.exists():
        raise BackupError('Database file nahi mili.')
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
    """Sirf apne banaye hue backup ka path (path traversal se bachav)."""
    if not NAME_RE.match(name or ''):
        return None
    p = backup_dir() / name
    return p if p.is_file() else None
