"""The Server health page: the figures never raise, are cached, warn at the right limits; the page is for the platform owner only.
The figure tests run everywhere; the page tests only with MULTI_SHOP=1 (they need the platform panel and PostgreSQL)."""
import os
from unittest import mock, skipUnless

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from tenants import serverstats as s

LINUX = os.path.exists('/proc/meminfo')


class TextTests(SimpleTestCase):
    def test_sizes(self):
        self.assertEqual(s.size_text(0), '0 bytes')
        self.assertEqual(s.size_text(1536), '1.5 KB')
        self.assertEqual(s.size_text(5 * 1024 ** 3), '5.0 GB')
        self.assertEqual(s.size_text(None), '0 bytes')

    def test_durations(self):
        self.assertEqual(s.duration_text(90), '1 min')
        self.assertEqual(s.duration_text(3 * 3600 + 120), '3 hours 2 min')
        self.assertEqual(s.duration_text(86400 + 3600), '1 day 1 hour')
        self.assertEqual(s.duration_text(-5), '0 min')

    def test_levels(self):
        self.assertEqual([s._level(v, s.DISK_USED) for v in (10, 84, 85, 94, 95, 100)], ['ok', 'ok', 'warn', 'warn', 'bad', 'bad'])
        self.assertEqual([s._level(v, s.MEM_AVAILABLE, higher_is_worse=False) for v in (50, 16, 15, 8, 7, 1)],
                         ['ok', 'ok', 'warn', 'warn', 'bad', 'bad'])


class FigureTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    @skipUnless(LINUX, 'needs Linux /proc')
    def test_the_figures_are_read(self):
        d = s.collect(force=True)
        for key in ('cpu', 'memory', 'disk', 'uptime', 'backup'):
            self.assertTrue(d[key]['ok'], key)
        self.assertGreaterEqual(d['cpu']['cpus'], 1)
        self.assertTrue(0 <= d['memory']['percent'] <= 100)
        self.assertTrue(0 <= d['disk']['percent'] <= 100)
        self.assertGreaterEqual(d['uptime']['app_seconds'], 0)
        self.assertIn('taken_at', d)

    def test_a_figure_that_fails_becomes_not_available_and_nothing_raises(self):
        names = ('cpu', 'memory', 'disk', 'uptime', 'database', 'connections', 'backup')
        with mock.patch.multiple(s, **{n: mock.Mock(side_effect=RuntimeError('boom')) for n in names}):
            d = s.collect(force=True)
        for n in names:
            self.assertEqual(d[n]['ok'], False, n)
        self.assertEqual(d['warnings'], [])
        self.assertEqual(s.worst_level(d), 'ok')

    def test_figures_are_cached_for_a_short_time(self):
        with mock.patch.object(s, 'cpu', wraps=s.cpu) as cpu:
            s.collect(force=True)
            s.collect()
            s.collect()
            self.assertEqual(cpu.call_count, 1)
            s.collect(force=True)
            self.assertEqual(cpu.call_count, 2)

    def test_measuring_is_quick(self):
        self.assertLess(s.collect(force=True)['took_ms'], 500)

    def test_warnings_at_the_limits(self):
        fine = dict(cpu=dict(ok=True, level='ok'), memory=dict(ok=True, level='ok'), disk=dict(ok=True, level='ok'),
                    connections=dict(ok=True, level='ok'), backup=dict(ok=True, level='ok', found=True))
        self.assertEqual(s._warnings(fine), [])
        bad = dict(fine, disk=dict(ok=True, level='bad', percent=96, free=100), backup=dict(ok=True, level='warn', found=True, text='2 days ago'),
                   memory=dict(ok=True, level='warn', available=1024), cpu=dict(ok=True, level='warn', text='load 9 of 4 CPUs'),
                   connections=dict(ok=True, level='bad', text='99 of 100 in use'))
        out = s._warnings(bad)
        self.assertEqual(len(out), 5)
        self.assertEqual(s.worst_level(dict(warnings=out)), 'bad')
        self.assertEqual(s.worst_level(dict(warnings=[w for w in out if w['level'] == 'warn'])), 'warn')
        self.assertIn('96% full', ' '.join(w['text'] for w in out))

    def test_no_backup_is_a_problem(self):
        with mock.patch('backend.backup.list_backups', return_value=[]):
            b = s.backup()
        self.assertEqual((b['level'], b['found']), ('bad', False))
        self.assertIn('No backup yet', s._warnings(dict(backup=b))[0]['text'] + b['text'])

    def test_a_missing_function_of_a_figure_does_not_break_the_others(self):
        with mock.patch.object(s, 'memory', side_effect=OSError('no /proc')):
            d = s.collect(force=True)
        self.assertFalse(d['memory']['ok'])
        self.assertTrue(d['disk']['ok'])


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class ServerPageTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        connection.set_schema_to_public()
        cache.clear()
        self.addCleanup(cache.clear)
        self.boss = get_user_model().objects.create_superuser('boss-server', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.boss)

    def test_the_page_opens_and_shows_the_figures(self):
        r = self.client.get('/superadmin/server/')
        self.assertEqual(r.status_code, 200)
        for text in ('Server health', 'CPU', 'Memory', 'Disk', 'Database size', 'Last backup'):
            self.assertContains(r, text)
        self.assertContains(self.client.get('/superadmin/'), 'href="/superadmin/server/"')       # the menu link

    def test_the_page_opens_when_every_figure_is_unreadable(self):
        names = ('cpu', 'memory', 'disk', 'uptime', 'database', 'connections', 'backup')
        with mock.patch.multiple(s, **{n: mock.Mock(side_effect=RuntimeError('boom')) for n in names}):
            r = self.client.get('/superadmin/server/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Not available')

    def test_measure_again_is_a_post_and_redirects(self):
        self.assertEqual(self.client.post('/superadmin/server/').status_code, 302)

    def test_only_a_platform_admin_can_open_it(self):
        from django.contrib.auth import get_user_model
        self.client.logout()
        self.assertEqual(self.client.get('/superadmin/server/').status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('plain-server', 'p@example.com', 'pw-12345-xx'))
        self.assertEqual(self.client.get('/superadmin/server/').status_code, 404)
        self.assertEqual(self.client.post('/superadmin/server/').status_code, 404)

    def test_the_shop_list_has_a_warning_when_the_server_needs_attention(self):
        warn = dict(level='bad', text='The disk is 97% full, only 1.0 GB is left.')
        data = dict(s.collect(force=True), warnings=[warn])
        with mock.patch.object(s, 'collect', return_value=data):
            r = self.client.get('/superadmin/')
        self.assertContains(r, 'Server needs attention')
        self.assertContains(r, 'The disk is 97% full')

    def test_the_shop_list_still_opens_if_the_server_figures_fail(self):
        with mock.patch.object(s, 'collect', side_effect=RuntimeError('boom')):
            self.assertEqual(self.client.get('/superadmin/').status_code, 200)

    def test_postgres_figures(self):
        db, conn = s.database(), s.connections()
        self.assertTrue(db['ok'] and db['total'] > 0)
        self.assertTrue(any(x['schema'] == 'public' for x in db['shops']))
        self.assertTrue(conn['ok'] and 1 <= conn['used'] <= conn['limit'])
