"""How busy the server is, for the platform owner's "Server health" page: CPU load, memory, disk, the database (total and per shop),
database connections, how long the application has been running and how old the last backup is.

Only reads. Nothing here ever raises: a figure that cannot be read becomes {'ok': False, 'error': ...} and the page shows
"Not available" for it. The live figures are cached for 30 seconds and the per-shop sizes for 10 minutes (the cache is shared by all
workers), so opening or refreshing the page again and again costs the server nothing."""
import logging
import os
import shutil
import time
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db import connection

log = logging.getLogger(__name__)
LIVE_KEY, DB_KEY = 'serverstats-live-v1', 'serverstats-db-v1'
LIVE_SECONDS, DB_SECONDS = 30, 600

# (warn, bad) limits
DISK_USED = (85, 95)               # per cent of the disk used
MEM_AVAILABLE = (15, 7)            # per cent of memory still available (less is worse)
LOAD_PER_CPU = (1.0, 2.0)          # load average of the last minute divided by the number of CPUs
CONN_USED = (80, 95)               # per cent of the allowed database connections
BACKUP_HOURS = (36, 72)


def size_text(n):
    """1536 -> '1.5 KB'."""
    n = float(n or 0)
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{int(n)} bytes' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024


def duration_text(seconds):
    """93784 -> '1 day 2 hours'."""
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f'{days} day{"" if days == 1 else "s"} {hours} hour{"" if hours == 1 else "s"}'
    if hours:
        return f'{hours} hour{"" if hours == 1 else "s"} {minutes} min'
    return f'{minutes} min'


def _level(value, limits, higher_is_worse=True):
    warn, bad = limits
    if higher_is_worse:
        return 'bad' if value >= bad else 'warn' if value >= warn else 'ok'
    return 'bad' if value <= bad else 'warn' if value <= warn else 'ok'


def _safe(name, fn):
    try:
        return fn()
    except Exception as e:           # a figure that cannot be read must never break the page
        log.warning('Server figure "%s" could not be read: %s', name, e)
        return {'ok': False, 'error': 'Not available on this server'}


def _read(path):
    with open(path) as f:
        return f.read()


def cpu():
    one, five, fifteen = os.getloadavg()
    cpus = os.cpu_count() or 1
    ratio = one / cpus
    return dict(ok=True, load1=round(one, 2), load5=round(five, 2), load15=round(fifteen, 2), cpus=cpus,
                percent=min(100, round(ratio * 100)), level=_level(ratio, LOAD_PER_CPU),
                text=f'load {one:.2f} of {cpus} CPU{"" if cpus == 1 else "s"}')


def memory():
    info = {}
    for line in _read('/proc/meminfo').splitlines():
        key, _, rest = line.partition(':')
        info[key] = int(rest.split()[0]) * 1024
    total, avail = info['MemTotal'], info.get('MemAvailable', info.get('MemFree', 0))
    used = total - avail
    free_pct = 100 * avail / total if total else 0
    swap_used = info.get('SwapTotal', 0) - info.get('SwapFree', 0)
    return dict(ok=True, total=total, available=avail, used=used, percent=round(100 - free_pct),
                level=_level(free_pct, MEM_AVAILABLE, higher_is_worse=False), swap_used=swap_used,
                text=f'{size_text(used)} used of {size_text(total)}')


def disk():
    path = str(settings.BASE_DIR)
    u = shutil.disk_usage(path)
    pct = 100 * u.used / (u.used + u.free) if (u.used + u.free) else 0      # like `df`: the part reserved for the system is not counted
    return dict(ok=True, total=u.total, used=u.used, free=u.free, percent=round(pct), level=_level(pct, DISK_USED),
                text=f'{size_text(u.free)} free of {size_text(u.total)}')


def _ticks_age(pid):
    """Seconds since process `pid` started (Linux), or None."""
    stat = _read(f'/proc/{pid}/stat')
    start_ticks = int(stat.rsplit(')', 1)[1].split()[19])
    uptime = float(_read('/proc/uptime').split()[0])
    return uptime - start_ticks / os.sysconf('SC_CLK_TCK'), uptime


def uptime():
    """How long the application has been running: the age of the gunicorn master process (the parent of this worker),
    otherwise of this process. Also how long the whole server has been up."""
    pid = os.getpid()
    try:
        parent = os.getppid()
        if b'gunicorn' in open(f'/proc/{parent}/cmdline', 'rb').read():
            pid = parent
    except OSError:
        pass
    age, boot = _ticks_age(pid)
    return dict(ok=True, app_seconds=int(age), server_seconds=int(boot), app_text=duration_text(age), server_text=duration_text(boot))


def _is_postgres():
    return 'postgresql' in connection.settings_dict['ENGINE']


def database():
    """Total size, per-shop sizes (cached for 10 minutes) and connections."""
    if not _is_postgres():
        from backend import backup
        path = backup._db_path()
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        return dict(ok=True, engine='sqlite', total=size, text=size_text(size), shops=[], connections=None)
    cached = cache.get(DB_KEY)
    if cached is None:
        with connection.cursor() as cur:
            cur.execute('SELECT pg_database_size(current_database())')
            total = int(cur.fetchone()[0])
            cur.execute("""SELECT schemaname, COALESCE(SUM(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename))), 0)::bigint
                           FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') AND schemaname !~ '^pg_'
                           GROUP BY schemaname ORDER BY 2 DESC""")
            schemas = [(name, int(size)) for name, size in cur.fetchall()]
        cached = dict(total=total, schemas=schemas)
        cache.set(DB_KEY, cached, DB_SECONDS)
    names = {}
    try:
        from .models import Shop
        names = {s.schema_name: s.name for s in Shop.objects.only('schema_name', 'name')}
    except Exception:
        log.warning('Shop names could not be read for the size list')
    total = cached['total']
    shops = [dict(schema=name, name=names.get(name) or ('Main site (My shop)' if name == 'public' else name), size=size,
                  text=size_text(size), percent=round(100 * size / total) if total else 0) for name, size in cached['schemas']]
    return dict(ok=True, engine='postgresql', total=total, text=size_text(total), shops=shops[:50], shops_hidden=max(0, len(shops) - 50),
                connections=None)


def connections():
    if not _is_postgres():
        return dict(ok=False, error='Only for PostgreSQL')
    with connection.cursor() as cur:
        cur.execute('SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()')
        used = int(cur.fetchone()[0])
        cur.execute('SHOW max_connections')
        limit = int(cur.fetchone()[0])
    pct = 100 * used / limit if limit else 0
    return dict(ok=True, used=used, limit=limit, percent=round(pct), level=_level(pct, CONN_USED), text=f'{used} of {limit} in use')


def backup():
    from backend import backup as b
    found = b.list_backups()
    if not found:
        return dict(ok=True, found=False, level='bad', text='No backup yet')
    newest = found[0]
    age = datetime.now() - newest['when']
    hours = age.total_seconds() / 3600
    return dict(ok=True, found=True, hours=round(hours, 1), level=_level(hours, BACKUP_HOURS), count=len(found),
                text=f'{duration_text(age.total_seconds())} ago ({newest["when"]:%d %b %Y %H:%M})', size=size_text(newest['size_kb'] * 1024))


def _warnings(d):
    out = []

    def add(level, text):
        out.append(dict(level=level, text=text))

    cpu_, mem, dsk, db, conn, bk = (d.get(k, {}) for k in ('cpu', 'memory', 'disk', 'database', 'connections', 'backup'))
    if cpu_.get('ok') and cpu_['level'] != 'ok':
        add(cpu_['level'], f"The server is busy: {cpu_['text']}. If this stays high for long, the shops will feel it slow.")
    if mem.get('ok') and mem['level'] != 'ok':
        add(mem['level'], f"Memory is running low: only {size_text(mem['available'])} is still available.")
    if dsk.get('ok') and dsk['level'] != 'ok':
        add(dsk['level'], f"The disk is {dsk['percent']}% full, only {size_text(dsk['free'])} is left. Old backups are the usual cause: remove the old ones.")
    if conn.get('ok') and conn['level'] != 'ok':
        add(conn['level'], f"Database connections are nearly all used ({conn['text']}).")
    if bk.get('ok') and bk['level'] != 'ok':
        add(bk['level'], 'There is no backup yet. Take one now.' if not bk.get('found') else f"The last backup is old ({bk['text']}). Check that the daily backup task is running.")
    return out


def collect(force=False):
    """All figures plus the warnings. Cached for 30 seconds (force=True measures again now)."""
    data = None if force else cache.get(LIVE_KEY)
    if data is None:
        started = time.perf_counter()
        data = {'cpu': _safe('cpu', cpu), 'memory': _safe('memory', memory), 'disk': _safe('disk', disk),
                'uptime': _safe('uptime', uptime), 'database': _safe('database', database),
                'connections': _safe('connections', connections), 'backup': _safe('backup', backup)}
        data['warnings'] = _warnings(data)
        data['taken_at'] = datetime.now().strftime('%d %b %Y %H:%M:%S')
        data['took_ms'] = round((time.perf_counter() - started) * 1000)
        cache.set(LIVE_KEY, data, LIVE_SECONDS)
    return data


def worst_level(data):
    """'bad', 'warn' or 'ok' for the whole server."""
    levels = {w['level'] for w in data.get('warnings', [])}
    return 'bad' if 'bad' in levels else 'warn' if 'warn' in levels else 'ok'
