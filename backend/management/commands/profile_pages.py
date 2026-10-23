"""Measures how many database queries and how much time each main page needs (read-only, GET requests only).

    python manage.py profile_pages            # the usual pages
    python manage.py profile_pages /gst/ /expiry/   # only these pages

Run it on a database with real data. A page with many queries, or the same query repeated many times,
is where the next speed-up is."""
import re
import time
from collections import Counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

PAGES = ['/', '/products/', '/sales/', '/purchases/', '/batches/', '/customers/', '/suppliers/', '/expenses/',
         '/report/', '/gst/', '/expiry/', '/reorder/', '/refill/', '/health/', '/supplier_prices/', '/drug_register/']


def _shape(sql):
    """The query without its numbers and quoted values, so repeated look-alike queries can be counted together."""
    return re.sub(r"'[^']*'|\b\d+\b", '?', sql)[:160]


class Command(BaseCommand):
    help = 'Shows the number of database queries and the time taken by each main page (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('paths', nargs='*', help='Pages to measure, e.g. /gst/ (default: the main pages)')

    def handle(self, *args, **opts):
        user = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
        if user is None:
            raise CommandError('Create a superuser first (python manage.py createsuperuser).')
        host = next((h for h in settings.ALLOWED_HOSTS if h and h[0] not in '.*['), 'localhost')
        client = Client(HTTP_HOST=host)
        client.force_login(user)
        self.stdout.write(f'{"page":22} {"status":6} {"queries":>7} {"ms":>7}  {"slowest query ms":>16}  repeated query')
        for path in opts['paths'] or PAGES:
            with CaptureQueriesContext(connection) as ctx:
                started = time.perf_counter()
                try:
                    resp = client.get(path, secure=True)
                    status = resp.status_code
                except Exception as e:  # noqa: BLE001  one broken page must not stop the others
                    status = type(e).__name__
                ms = (time.perf_counter() - started) * 1000
            if status == 404:
                continue
            times = [float(q['time']) * 1000 for q in ctx.captured_queries] or [0.0]
            repeat = Counter(_shape(q['sql']) for q in ctx.captured_queries).most_common(1)
            note = f'{repeat[0][1]}x {repeat[0][0]}' if repeat and repeat[0][1] >= 5 else ''
            self.stdout.write(f'{path:22} {status!s:6} {len(ctx.captured_queries):7d} {ms:7.0f}  {max(times):16.1f}  {note}')
        self.stdout.write('Many queries, or one query repeated 5+ times, means a page that can be made faster.')
