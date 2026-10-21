"""The health score of the shop and the app: only REAL measurements, each with the reason for every point lost.

  Data (40)      stock matches the batches, no negative batches, nothing without a unit
  Safety (25)    a recent backup that really opens, the database passes its own integrity check, debug is off
  Speed (20)     how long the main queries take on this server right now
  Business (15)  expired stock on the shelf, low stock, medicines without MRP / HSN

A section starts at 100 and loses points per finding. A check that cannot run is shown as "could not be checked"
and is left out of the score (it is never counted as good). Nothing here changes any data."""
import gzip
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from . import backup

WEIGHTS = {'data': 40, 'safety': 25, 'speed': 20, 'business': 15}
TITLES = {'data': 'Data', 'safety': 'Safety', 'speed': 'Speed', 'business': 'Shop'}
HISTORY_KEEP = 120


def chk(status, title, detail, lost=0, items=None):
    """status: ok / warn / bad / info / unknown."""
    return dict(status=status, title=title, detail=detail, lost=int(lost), items=list(items or [])[:8])


def label_for(score):
    if score >= 90:
        return 'Excellent', 'green'
    if score >= 75:
        return 'Good', 'green'
    if score >= 60:
        return 'Needs attention', 'amber'
    return 'At risk', 'red'


def _safe(title, fn):
    try:
        return fn()
    except Exception:                    # a check must never break the page: it is shown as "could not be checked"
        return [chk('unknown', title, 'Could not be checked right now.')]


# ---------------------------------------------------------------- data
def data_checks():
    from .models import Batch, Product

    def stock_vs_batches():
        sums = {r['product_id']: r['s'] or 0 for r in Batch.objects.exclude(product__isnull=True).values('product_id').annotate(s=Sum('qty')).order_by()}
        prods = {pk: (name, stock) for pk, name, stock in Product.objects.values_list('id', 'name', 'stock_qty')}
        bad = [(prods[pk][0] or f'#{pk}', prods[pk][1], s) for pk, s in sums.items() if pk in prods and prods[pk][1] != s]
        if not bad:
            return [chk('ok', 'Stock matches the batches', f'The stock of all {len(prods)} products agrees with their batches.')]
        bad.sort(key=lambda x: str(x[0]))
        return [chk('bad', 'Stock does not match the batches',
                    f'{len(bad)} product(s) show a different stock than their batches add up to.', min(50, 10 * len(bad)),
                    [f'{n}: stock {st}, batches {b}' for n, st, b in bad])]

    def negative():
        n = Batch.objects.filter(qty__lt=0).count()
        if not n:
            return [chk('ok', 'No negative batch', 'No batch has a negative quantity.')]
        return [chk('bad', 'Negative batch', f'{n} batch(es) have a negative quantity.', min(30, 15 * n))]

    def without_batch():
        has = set(Batch.objects.exclude(product__isnull=True).values_list('product_id', flat=True).distinct())
        qs = Product.objects.filter(stock_qty__gt=0).exclude(pk__in=has)
        n = qs.count()
        if not n:
            return [chk('ok', 'Every stocked product has a batch', 'Expiry is tracked for all the stock.')]
        return [chk('warn', 'Stock without a batch record', f'{n} product(s) have stock but no batch / expiry record.', min(20, 2 * n),
                    list(qs.order_by('name').values_list('name', flat=True)[:8]))]

    def no_unit():
        n = Product.objects.filter(unit__isnull=True).count()
        if not n:
            return [chk('ok', 'Every product has a base unit', 'All products have a base unit.')]
        return [chk('warn', 'Products without a base unit', f'{n} product(s) have no base unit.', min(10, 2 * n))]

    out = []
    for title, fn in (('Stock vs batches', stock_vs_batches), ('Negative batches', negative),
                      ('Stock without batch', without_batch), ('Base units', no_unit)):
        out += _safe(title, fn)
    return out


# ---------------------------------------------------------------- safety
def _verify_backup(path):
    """Opens a backup file and runs the database's own check on it. Returns '' when fine, else the problem."""
    if str(path).endswith('.pgdump'):                   # PostgreSQL custom format: a non-empty file that starts with PGDMP
        try:
            with open(path, 'rb') as f:
                head = f.read(5)
        except OSError as exc:
            return str(exc)[:120]
        return '' if head == b'PGDMP' else 'the file is empty or is not a PostgreSQL backup'
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / 'check.sqlite3'
        try:
            with gzip.open(path, 'rb') as f_in, open(snap, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        except (OSError, EOFError) as exc:            # not a valid .gz file
            return str(exc)[:120]
        try:
            con = sqlite3.connect(snap)
            try:
                row = con.execute('PRAGMA quick_check').fetchone()
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:          # "file is not a database" and similar
            return str(exc)[:120]
    return '' if row and row[0] == 'ok' else str(row[0] if row else 'no answer')[:120]


def safety_checks():
    def backups():
        found = backup.list_backups()
        if not found:
            return [chk('bad', 'No backup yet', 'No backup has been taken. Take one on the Backup page and schedule the daily task.', 50)]
        newest = found[0]
        age = datetime.now() - newest['when']
        hours = int(age.total_seconds() // 3600)
        txt = f"{newest['when']:%d %b %Y %H:%M} ({hours} hours ago)" if hours < 48 else f"{newest['when']:%d %b %Y %H:%M} ({age.days} days ago)"
        if age <= timedelta(hours=36):
            res = [chk('ok', 'Recent backup', f'Latest backup: {txt}. {len(found)} kept.')]
        elif age <= timedelta(days=3):
            res = [chk('warn', 'Backup is getting old', f'Latest backup: {txt}.', 15)]
        else:
            res = [chk('bad', 'Backup is old', f'Latest backup: {txt}. Take a new one now.', 40)]
        if newest['size_kb'] > 300000:
            res.append(chk('info', 'Backup not opened', 'The backup is large, so it was not opened for checking.'))
        else:
            path = backup.backup_path(newest['name'])
            problem = _verify_backup(path) if path else 'file not found'
            res.append(chk('ok', 'Backup opens correctly', 'The latest backup was opened and its database passed the check.') if not problem
                       else chk('bad', 'Backup is damaged', f'The latest backup did not pass the check: {problem}', 40))
        return res

    def integrity():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
            return [chk('ok', 'Database is healthy', 'PostgreSQL is running and answering.')]
        if connection.vendor != 'sqlite':
            return [chk('info', 'Database check', 'The built-in integrity check is for SQLite and PostgreSQL only.')]
        with connection.cursor() as cur:
            cur.execute('PRAGMA quick_check')
            row = cur.fetchone()
        if row and row[0] == 'ok':
            return [chk('ok', 'Database is healthy', 'The database passed its own integrity check.')]
        return [chk('bad', 'Database problem', f"The integrity check says: {str(row[0] if row else 'no answer')[:120]}", 60)]

    def settings_check():
        if settings.DEBUG:
            return [chk('bad', 'Debug mode is ON', 'Debug mode shows internal details to visitors. Turn it off on the live site.', 30)]
        if not (getattr(settings, 'SESSION_COOKIE_SECURE', False) and getattr(settings, 'SECURE_SSL_REDIRECT', False)):
            return [chk('warn', 'Secure connection not forced', 'Logins are not forced onto https.', 10)]
        return [chk('ok', 'Secure settings', 'Debug is off and https is forced for logins.')]

    out = []
    for title, fn in (('Backup', backups), ('Database check', integrity), ('Settings', settings_check)):
        out += _safe(title, fn)
    return out


# ---------------------------------------------------------------- speed
def _median_ms(fn, runs=3):
    fn()                                              # warm-up (first call is slower)
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    return times[len(times) // 2]


def speed_checks():
    from .models import Batch, Bill, Product, Sale

    today = timezone.localdate()

    def roundtrip():
        with connection.cursor() as cur:
            for _ in range(10):
                cur.execute('SELECT 1')
                cur.fetchone()

    tests = (
        ('Database answer time', 'a simple query', roundtrip, 20, 80),
        ('Product list', '50 products', lambda: list(Product.objects.select_related('unit', 'brand').order_by('name')[:50]), 150, 400),
        ('Recent bills', '30 bills with their items', lambda: list(Bill.objects.prefetch_related('items').order_by('-id')[:30]), 200, 500),
        ('Stock and expiry', 'all batches in stock', lambda: Batch.objects.filter(qty__gt=0).aggregate(n=Count('id'), q=Sum('qty')), 150, 400),
        ("Today's sales", 'the total of today', lambda: Sale.objects.filter(date=today).aggregate(s=Sum('total')), 100, 300),
    )
    out = []
    for title, what, fn, good, slow in tests:
        def one(title=title, what=what, fn=fn, good=good, slow=slow):
            ms = _median_ms(fn)
            txt = f'{what}: {ms:.0f} ms (measured now on this server).'
            if ms <= good:
                return [chk('ok', title, txt)]
            if ms <= slow:
                return [chk('warn', title, txt + ' A little slow.', 10)]
            return [chk('bad', title, txt + ' Slow.', 25)]
        out += _safe(title, one)
    return out


# ---------------------------------------------------------------- shop
def business_checks():
    from .models import Batch, Product

    today = timezone.localdate()

    def expired():
        qs = Batch.objects.filter(qty__gt=0, expire_date__lt=today).select_related('product')
        rows = list(qs.order_by('expire_date')[:200])
        if not rows:
            return [chk('ok', 'No expired stock', 'No expired batch is left on the shelf.')]
        units = sum(b.qty for b in rows)
        return [chk('warn', 'Expired stock on the shelf', f'{len(rows)} batch(es) ({units} units) are expired. Write them off or return them.',
                    min(40, 5 * len(rows)),
                    [f"{b.product.name if b.product else '-'} · {b.batch_number} · {b.qty} units (exp {b.expire_date:%d %b %Y})" for b in rows])]

    def expiring():
        n = Batch.objects.filter(qty__gt=0, expire_date__gte=today, expire_date__lte=today + timedelta(days=30)).count()
        if not n:
            return [chk('ok', 'Nothing expires in 30 days', 'No batch expires within 30 days.')]
        return [chk('info', 'Expiring soon', f'{n} batch(es) expire within 30 days. See Expiry Returns.', min(15, n))]

    def stock_levels():
        active = Product.objects.exclude(is_active=False)
        total = active.count()
        if not total:
            return [chk('info', 'No products yet', 'Add products by entering a purchase.')]
        out_n = active.filter(stock_qty=0).count()
        low_n = active.filter(stock_qty__gt=0, stock_qty__lte=F('reorder_level')).count()
        share = (out_n + low_n) / total
        txt = f'{out_n} out of stock and {low_n} low, of {total} products.'
        if share > 0.3:
            return [chk('warn', 'Many medicines are out or low', txt + ' See Reorder Suggestions.', 10)]
        return [chk('ok', 'Stock levels are fine', txt)]

    def master():
        active = Product.objects.exclude(is_active=False)
        no_mrp = active.filter(stock_qty__gt=0, mrp__isnull=True)
        n = no_mrp.count()
        res = []
        res.append(chk('ok', 'Every stocked medicine has an MRP', 'Billing can fill the rate for all of them.') if not n else
                   chk('warn', 'Medicines without an MRP', f'{n} stocked medicine(s) have no MRP, so the billing rate must be typed.',
                       min(25, 2 * n), list(no_mrp.order_by('name').values_list('name', flat=True)[:8])))
        h = active.filter(Q(hsn_code__isnull=True) | Q(hsn_code='')).count()
        res.append(chk('ok', 'Every medicine has an HSN', 'HSN is filled for all products.') if not h else
                   chk('warn', 'Medicines without an HSN', f'{h} medicine(s) have no HSN code (needed for the GST report).', min(15, h)))
        return res

    out = []
    for title, fn in (('Expired stock', expired), ('Expiring stock', expiring), ('Stock levels', stock_levels), ('Master data', master)):
        out += _safe(title, fn)
    return out


# ---------------------------------------------------------------- the score
def section_score(checks):
    real = [c for c in checks if c['status'] != 'unknown']
    if not real:
        return None
    return max(0, 100 - sum(c['lost'] for c in real))


def compute():
    """Measures everything now. Returns a JSON-safe dict."""
    from .models import Bill, Product, Purchase
    started = time.perf_counter()
    sections = []
    for key, fn in (('data', data_checks), ('safety', safety_checks), ('speed', speed_checks), ('business', business_checks)):
        try:
            checks = fn()
        except Exception:
            checks = [chk('unknown', TITLES[key], 'Could not be checked right now.')]
        sections.append(dict(key=key, title=TITLES[key], weight=WEIGHTS[key], score=section_score(checks), checks=checks))
    scored = [s for s in sections if s['score'] is not None]
    weight = sum(s['weight'] for s in scored)
    score = round(sum(s['score'] * s['weight'] for s in scored) / weight) if weight else 0
    label, tone = label_for(score)
    stats = {}
    try:
        stats = dict(products=Product.objects.count(), bills=Bill.objects.count(), purchases=Purchase.objects.count())
        if connection.vendor == 'sqlite':
            name = Path(settings.DATABASES['default']['NAME'])
            stats['db_mb'] = round(name.stat().st_size / 1048576, 1) if name.exists() else 0
        elif connection.vendor == 'postgresql':
            with connection.cursor() as cur:
                cur.execute('SELECT pg_database_size(current_database())')
                stats['db_mb'] = round(cur.fetchone()[0] / 1048576, 1)
    except Exception:
        stats = {}
    return dict(score=score, label=label, tone=tone, sections=sections, stats=stats,
                checked_at=timezone.now().isoformat(), took_ms=int((time.perf_counter() - started) * 1000),
                unchecked=sum(1 for s in sections for c in s['checks'] if c['status'] == 'unknown'))


def refresh():
    """Measures now, remembers it (the last HISTORY_KEEP are kept) and returns the data."""
    from .models import HealthSnapshot
    data = compute()
    snap = HealthSnapshot.objects.create(score=data['score'], data=data)
    old = list(HealthSnapshot.objects.values_list('id', flat=True)[HISTORY_KEEP:])
    if old:
        HealthSnapshot.objects.filter(id__in=old).delete()
    data['snapshot_id'] = snap.pk
    return data


def current(max_age_minutes=60):
    """The latest measurement, or a new one when it is older than `max_age_minutes` (or there is none)."""
    from .models import HealthSnapshot
    snap = HealthSnapshot.objects.first()
    if snap and timezone.now() - snap.created_at <= timedelta(minutes=max_age_minutes):
        return snap.data
    return refresh()


def history(limit=30):
    from .models import HealthSnapshot
    rows = list(HealthSnapshot.objects.values_list('created_at', 'score')[:limit])
    rows.reverse()
    return [dict(when=timezone.localtime(t).strftime('%d %b %H:%M'), score=s) for t, s in rows]
