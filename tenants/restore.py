"""Restore one shop from one of its own backups, without ever touching the live data until the new data is proven good.

How it works (PostgreSQL only; a shop is one schema):
  1. The shop is switched to a "please wait" page (Shop.restoring) and given a few seconds to finish what it was doing.
  2. A safety backup of the shop as it is now is taken. If that fails, nothing else happens.
  3. The live schema is only RENAMED (s_shop -> bak_<time>_s_shop). Nothing is deleted.
  4. The backup is loaded with pg_restore --single-transaction, which creates a fresh s_shop (all or nothing).
  5. The new schema is checked (the main tables exist, the numbers are read) and migrations are applied to it.
  6. Anything wrong at any step: the half-made schema is dropped and the old one gets its name back. The shop is exactly as before.
  7. The old data stays in bak_... for KEEP_DAYS days, so "Undo" is one rename away. After that it is removed.

The few PostgreSQL calls are small functions at the top, so the order of the steps can be tested without PostgreSQL."""
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from backend import backup

from .models import RestoreRequest, Shop

log = logging.getLogger(__name__)
KEEP_DAYS = 7
STUCK_MINUTES = 90                 # a restore "running" for longer than this is shown as stuck, with a Recover button
RESTORE_TIMEOUT = 3600
OLD_RE = re.compile(r'^bak_\d{14}(?:_\d)?_[a-z0-9_]+$')
REQUIRED_TABLES = ('django_migrations', 'auth_user', 'backend_product', 'backend_bill')
COUNTED = (('backend_product', 'products'), ('backend_bill', 'bills'), ('backend_customer', 'customers'))


class RestoreError(Exception):
    """A problem that can be told to the platform owner as it is."""


# ---------------------------------------------------------------- the few PostgreSQL calls

def parked_name(schema):
    """A name for the saved copy of a shop's data: bak_<date and time to the second>_<schema>. Never one that already exists."""
    base = f"bak_{timezone.now():%Y%m%d%H%M%S}_{schema}"
    name, n = base[:63], 1
    while schema_exists(name):
        n += 1
        name = f"bak_{timezone.now():%Y%m%d%H%M%S}_{n}_{schema}"[:63]
    return name


def _quote(name):
    return connection.ops.quote_name(name)


def schema_exists(name):
    with connection.cursor() as cur:
        cur.execute('SELECT 1 FROM information_schema.schemata WHERE schema_name = %s', [name])
        return cur.fetchone() is not None


def rename_schema(old, new):
    with connection.cursor() as cur:
        cur.execute(f'ALTER SCHEMA {_quote(old)} RENAME TO {_quote(new)}')


def swap_schemas(live, other, parked):
    """live -> parked and other -> live, in one transaction: both happen or neither does."""
    from django.db import transaction
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(f'ALTER SCHEMA {_quote(live)} RENAME TO {_quote(parked)}')
        cur.execute(f'ALTER SCHEMA {_quote(other)} RENAME TO {_quote(live)}')


def drop_schema(name):
    if not OLD_RE.match(name) and not name.startswith('s_'):
        raise RestoreError('Refusing to drop a schema this feature did not make.')
    with connection.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS {_quote(name)} CASCADE')


def tables_of(schema):
    with connection.cursor() as cur:
        cur.execute('SELECT table_name FROM information_schema.tables WHERE table_schema = %s', [schema])
        return {r[0] for r in cur.fetchall()}


def counts(schema):
    """{'products': 120, 'bills': 340, ...} read from the schema; a missing table is left out."""
    have = tables_of(schema)
    out = {}
    with connection.cursor() as cur:
        for table, label in COUNTED:
            if table in have:
                cur.execute(f'SELECT COUNT(*) FROM {_quote(schema)}.{_quote(table)}')
                out[label] = cur.fetchone()[0]
    return out


def dump_schemas(path):
    """The schema names a backup file creates (read with pg_restore --list; nothing is loaded)."""
    exe = backup.find_pg_restore()
    done = subprocess.run([exe, '--list', str(path)], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RestoreError(f"pg_restore could not read the backup: {(done.stderr or '').strip()[:200]}")
    return set(re.findall(r'\bSCHEMA - (\S+)', done.stdout))


def load_dump(path):
    """Loads the backup in ONE transaction: it is fully loaded or not at all."""
    exe = backup.find_pg_restore()
    db = settings.DATABASES['default']
    cmd = [exe, '--single-transaction', '--no-owner', '--no-privileges', '-h', str(db.get('HOST') or 'localhost'),
           '-p', str(db.get('PORT') or '5432'), '-U', str(db.get('USER') or ''), '-d', str(db['NAME']), str(path)]
    env = dict(os.environ, PGPASSWORD=str(db.get('PASSWORD') or ''))
    try:
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=RESTORE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RestoreError(f'pg_restore could not run: {str(e)[:150]}')
    if done.returncode != 0:
        raise RestoreError(f"pg_restore failed: {(done.stderr or 'no message').strip()[:250]}")


def migrate(schema):
    call_command('migrate_schemas', schema_name=schema, interactive=False, verbosity=0)


# ---------------------------------------------------------------- checks

def check_ready(shop, backup_name, claimed=False):
    """Everything that can be checked before anything is touched. Raises RestoreError with a plain message. Returns the file path.
    claimed=True: the request was already started, so the shop is marked as restoring on purpose."""
    if not backup.is_postgres():
        raise RestoreError('Restoring a shop works only with PostgreSQL.')
    if shop.schema_name == settings.PUBLIC_SCHEMA_NAME:
        raise RestoreError('The main shop cannot be restored from here.')
    if shop.restoring and not claimed:
        raise RestoreError('This shop is already being restored.')
    path = backup.backup_path(backup_name, shop.schema_name)
    if not path:
        raise RestoreError('That backup file was not found for this shop.')
    size = path.stat().st_size
    with open(path, 'rb') as f:
        if f.read(5) != b'PGDMP':
            raise RestoreError('That file is not a PostgreSQL backup.')
    if not backup.find_pg_restore():
        raise RestoreError('pg_restore was not found. Install the PostgreSQL tools, or set PG_RESTORE in .env to its full path.')
    free = shutil.disk_usage(backup.backup_dir()).free
    if free < size * 4 + 100 * 1024 * 1024:
        raise RestoreError('There is not enough free disk space to restore safely. Free some space first.')
    if not schema_exists(shop.schema_name):
        raise RestoreError('The shop has no data schema.')
    found = dump_schemas(path)
    if found != {shop.schema_name}:
        raise RestoreError('That backup does not belong to this shop, so it was not used.')
    return path


def verify(schema):
    """Problems found in a freshly restored schema (an empty list when it looks right)."""
    have = tables_of(schema)
    problems = [f'the table {t} is missing' for t in REQUIRED_TABLES if t not in have]
    if not problems:
        with connection.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM {_quote(schema)}.django_migrations')
            if not cur.fetchone()[0]:
                problems.append('the migration history is empty')
    return problems


def describe(c):
    return ', '.join(f'{v} {k}' for k, v in c.items()) or 'no data'


# ---------------------------------------------------------------- the steps

def start(req_id, spawn=True):
    """Claims a waiting request and starts the restore in a separate process (a restore takes minutes, a web request cannot wait).
    Returns the request. Raises RestoreError, leaving everything as it was, when it cannot start."""
    connection.set_schema_to_public()
    req = RestoreRequest.objects.select_related('shop').get(pk=req_id)
    if req.status != 'requested':
        raise RestoreError('This request is not waiting any more.')
    check_ready(req.shop, req.backup_name)
    claimed = Shop.objects.filter(pk=req.shop_id, restoring=False).update(restoring=True)
    if not claimed:
        raise RestoreError('This shop is already being restored.')
    now = timezone.now()
    if not RestoreRequest.objects.filter(pk=req.pk, status='requested').update(status='running', started_at=now, message=''):
        Shop.objects.filter(pk=req.shop_id).update(restoring=False)
        raise RestoreError('This request is not waiting any more.')
    if spawn:
        try:
            subprocess.Popen([sys.executable, str(settings.BASE_DIR / 'manage.py'), 'run_restore', str(req.pk)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(settings.BASE_DIR))
        except OSError as e:
            RestoreRequest.objects.filter(pk=req.pk).update(status='failed', message=f'The restore could not be started: {str(e)[:150]}',
                                                           finished_at=timezone.now())
            Shop.objects.filter(pk=req.shop_id).update(restoring=False)
            raise RestoreError('The restore could not be started.')
    req.refresh_from_db()
    return req


def _fail(req, text, rolled_back):
    note = ' Your shop is exactly as it was before.' if rolled_back else (
        ' The shop was NOT put back automatically: it stays on the "please wait" page until you press Recover.')
    RestoreRequest.objects.filter(pk=req.pk).update(status='failed', message=(text + note)[:1000], finished_at=timezone.now(),
                                                    **({'old_schema': ''} if rolled_back else {}))
    if rolled_back:
        Shop.objects.filter(pk=req.shop_id).update(restoring=False)


def rollback(req):
    """Puts the shop back exactly as it was. True when done. Safe to run again (it only looks at what exists now)."""
    connection.set_schema_to_public()
    schema, old = req.shop.schema_name, req.old_schema
    if not old:
        return True
    if schema_exists(old):
        if schema_exists(schema):
            drop_schema(schema)                      # the half-made copy of the restore
        rename_schema(old, schema)
    elif not schema_exists(schema):
        raise RestoreError('Neither the shop data nor its saved copy was found.')
    return True


def execute(req_id):
    """The restore itself. Runs in its own process (manage.py run_restore). Never leaves the shop half-restored."""
    connection.set_schema_to_public()
    req = RestoreRequest.objects.select_related('shop').get(pk=req_id)
    if req.status != 'running':
        raise RestoreError('This request was not started.')
    shop, schema = req.shop, req.shop.schema_name
    try:
        parked = parked_name(schema)
        path = check_ready(shop, req.backup_name, claimed=True)
        time.sleep(getattr(settings, 'RESTORE_DRAIN_SECONDS', 3))          # let requests already in progress finish
        from django_tenants.utils import schema_context
        with schema_context(schema):
            safety = backup.make_backup(keep=0)                            # the shop as it is now; if this fails nothing happens
        connection.set_schema_to_public()
        RestoreRequest.objects.filter(pk=req.pk).update(safety_backup=safety.name)
        before = counts(schema)
        RestoreRequest.objects.filter(pk=req.pk).update(old_schema=parked)  # written BEFORE the rename, so a crash can be undone
        req.old_schema = parked
        rename_schema(schema, parked)
        load_dump(path)
        problems = verify(schema)
        if problems:
            raise RestoreError('The restored data did not pass the check: ' + '; '.join(problems) + '.')
        migrate(schema)
        after = counts(schema)
    except Exception as e:
        log.exception('Restore of %s failed', schema)
        text = str(e) if isinstance(e, RestoreError) else f'Unexpected problem: {str(e)[:200]}'
        try:
            ok = rollback(req)
        except Exception:
            log.exception('Rollback of %s failed', schema)
            ok = False
        _fail(req, text, ok)
        return False
    finally:
        connection.set_schema_to_public()
    done = f'Restored from {req.backup_name}. Now: {describe(after)}. Before: {describe(before)}.'
    RestoreRequest.objects.filter(pk=req.pk).update(status='done', message=done, finished_at=timezone.now())
    Shop.objects.filter(pk=shop.pk).update(restoring=False)
    _after_swap(schema)
    _note(schema, f'Data restored from {req.backup_name}')
    return True


def _note(schema, text):
    """One line in the shop's own Audit log, so the owner can see who changed the data and when."""
    try:
        from django_tenants.utils import schema_context

        from backend import auditlog
        with schema_context(schema):
            auditlog.record('Symaso support', 'BACKUP', 'Restore', text)
    except Exception:
        log.exception('Could not write the restore note to the audit log of %s', schema)
    finally:
        connection.set_schema_to_public()


def _after_swap(schema):
    try:
        cache.clear()                                                      # nothing remembered from the old data is kept
    except Exception:
        log.exception('Could not clear the cache after restoring %s', schema)


def recover(req_id):
    """For a restore that is stuck (the server stopped in the middle): puts the shop back as it was and ends the request."""
    connection.set_schema_to_public()
    req = RestoreRequest.objects.select_related('shop').get(pk=req_id)
    if req.status not in ('running', 'failed') or not req.shop.restoring:
        raise RestoreError('Nothing to recover: this shop is not waiting for a restore to finish.')
    rollback(req)
    RestoreRequest.objects.filter(pk=req.pk).update(status='failed', old_schema='', finished_at=timezone.now(),
                                                    message='The restore was stopped and the shop was put back exactly as it was.')
    Shop.objects.filter(pk=req.shop_id).update(restoring=False)


def undo(req_id):
    """Goes back to the data from before a restore, while it is still kept. The restored data is kept the same way, so nothing is lost."""
    connection.set_schema_to_public()
    req = RestoreRequest.objects.select_related('shop').get(pk=req_id)
    shop, schema = req.shop, req.shop.schema_name
    if req.status != 'done' or not req.old_schema or not schema_exists(req.old_schema):
        raise RestoreError('The data from before this restore is no longer kept, so it cannot be undone.')
    if not Shop.objects.filter(pk=shop.pk, restoring=False).update(restoring=True):
        raise RestoreError('This shop is being restored right now.')
    try:
        parked = parked_name(schema)
        time.sleep(getattr(settings, 'RESTORE_DRAIN_SECONDS', 3))
        swap_schemas(schema, req.old_schema, parked)                        # one transaction
    except Exception:
        Shop.objects.filter(pk=shop.pk).update(restoring=False)
        log.exception('Undo of the restore of %s failed', schema)
        raise RestoreError('The undo did not work. The shop was not changed.')
    RestoreRequest.objects.filter(pk=req.pk).update(status='undone', old_schema=parked, finished_at=timezone.now(),
                                                    message='Undone: the shop has the data it had before the restore.')
    Shop.objects.filter(pk=shop.pk).update(restoring=False)
    _after_swap(schema)
    _note(schema, 'Restore undone: data from before the restore is back')


def cleanup(now=None):
    """Removes the saved copies that are older than KEEP_DAYS. Only schemas that a restore made (and recorded) are touched."""
    now = now or timezone.now()
    gone = []
    old = RestoreRequest.objects.filter(status__in=('done', 'undone'), finished_at__lt=now - timedelta(days=KEEP_DAYS)).exclude(old_schema='')
    for req in old:
        name = req.old_schema
        if not OLD_RE.match(name):
            continue
        try:
            drop_schema(name)
        except Exception:
            log.exception('Could not remove the saved copy %s', name)
            continue
        RestoreRequest.objects.filter(pk=req.pk).update(old_schema='')
        gone.append(name)
    return gone


def can_undo(req, now=None):
    now = now or timezone.now()
    return bool(req.status == 'done' and req.old_schema and req.finished_at and now - req.finished_at < timedelta(days=KEEP_DAYS))


def is_stuck(req, now=None):
    now = now or timezone.now()
    return bool(req.status == 'running' and req.started_at and now - req.started_at > timedelta(minutes=STUCK_MINUTES))
