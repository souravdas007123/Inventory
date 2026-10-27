"""Maintenance mode: the window file, the shop page, the platform page, the e-mail and the server command.
Runs only with MULTI_SHOP=1 (the shop tests need PostgreSQL schemas)."""
import json
import os
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

if settings.MULTI_SHOP:
    from tenants import maintenance, maintenance_admin
    from tenants.models import Shop

GOOD = dict(shop_name='Maint Medical', owner_name='Raj Sharma', email='raj@example.com', phone='98765 43210',
            subdomain='maint-test', password='Str0ng-pass-77', password2='Str0ng-pass-77', agree='1')
SCHEMA = 's_maint_test'
HOST = 'maint-test.localhost'
SAFE = dict(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost', SECURE_SSL_REDIRECT=False,
            SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SIGNUP_SKIP_EMAIL=False)


class WithFile:
    """Every test gets its own empty folder for the maintenance file."""

    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, 'maintenance.json')
        override = override_settings(MAINTENANCE_FILE=self.path)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.folder, True)
        maintenance._cache.update(sig=None, data=None)


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
class WindowTests(WithFile, SimpleTestCase):
    def now(self):
        return timezone.now()

    def test_no_file_means_no_maintenance(self):
        self.assertEqual(maintenance.state()[0], 'off')
        self.assertFalse(maintenance.is_active())
        self.assertEqual((maintenance.banner(), maintenance.platform_bar()), ('', ''))

    def test_a_planned_window_goes_scheduled_then_active_then_off(self):
        now = self.now()
        maintenance.schedule(now + timedelta(hours=1), now + timedelta(hours=2), 'New features')
        self.assertEqual(maintenance.state(now)[0], 'scheduled')
        self.assertFalse(maintenance.is_active(now))
        self.assertIn('Planned maintenance', maintenance.banner(now))
        self.assertIn('New features', maintenance.banner(now))
        inside = now + timedelta(hours=1, minutes=5)
        self.assertEqual(maintenance.state(inside)[0], 'active')
        self.assertEqual(maintenance.banner(inside), '')                 # inside the window shops get the page, not the banner
        self.assertIn('Maintenance is ON', maintenance.platform_bar(inside))
        self.assertEqual(maintenance.state(now + timedelta(hours=2, seconds=1))[0], 'off')   # ends by itself

    def test_start_now_and_end_now(self):
        maintenance.start_now(20, '  Short   update ')
        self.assertTrue(maintenance.is_active())
        self.assertEqual(maintenance.window()['message'], 'Short update')
        self.assertTrue(maintenance.end_now())
        self.assertFalse(maintenance.is_active())
        self.assertFalse(maintenance.end_now())

    def test_a_damaged_file_never_locks_the_shops(self):
        for text in ('', 'not json', '{}', '{"start": "x", "end": "y"}',
                     json.dumps(dict(start='2026-01-02T00:00:00+00:00', end='2026-01-01T00:00:00+00:00'))):
            with open(self.path, 'w') as f:
                f.write(text)
            maintenance._cache.update(sig=None, data=None)
            self.assertFalse(maintenance.is_active(), text)
            self.assertEqual(maintenance.banner(), '')

    def test_a_change_made_by_another_process_is_seen(self):
        maintenance.start_now(10)
        self.assertTrue(maintenance.is_active())
        now = self.now()
        with open(self.path, 'w') as f:                                  # what the command line does, from outside
            json.dump(dict(start=(now + timedelta(days=1)).isoformat(), end=(now + timedelta(days=1, hours=1)).isoformat(),
                           message='Later, in a longer text so the size changes'), f)
        self.assertFalse(maintenance.is_active())
        self.assertEqual(maintenance.state()[0], 'scheduled')

    def test_extend_moves_the_end_but_not_past_twelve_hours(self):
        self.assertIsNone(maintenance.extend(15))
        maintenance.start_now(30)
        start = maintenance.window()['start']
        end = maintenance.extend(15)
        self.assertEqual(end - start, timedelta(minutes=45))
        end = maintenance.extend(maintenance.MAX_MINUTES)
        self.assertEqual(end - start, timedelta(minutes=maintenance.MAX_MINUTES))

    def test_writing_leaves_no_temporary_file_behind(self):
        maintenance.start_now(10)
        maintenance.extend(5)
        self.assertEqual(os.listdir(self.folder), ['maintenance.json'])

    def test_the_form_is_checked(self):
        now = self.now()
        local = timezone.localtime(now + timedelta(days=1))
        good = dict(date=f'{local:%Y-%m-%d}', time='23:00', minutes='30', message='Update')
        start, end, message, errors = maintenance.parse_schedule(good, now)
        self.assertEqual(errors, [])
        self.assertEqual(end - start, timedelta(minutes=30))
        self.assertEqual(start.utcoffset(), timedelta(hours=5, minutes=30))        # the time is understood as Indian time
        self.assertEqual(timezone.localtime(start).strftime('%H:%M'), '23:00')
        for change, word in ((dict(minutes='4'), 'between'), (dict(minutes='721'), 'between'), (dict(minutes='x'), 'minutes'),
                             (dict(date='2020-01-01'), 'already over'), (dict(date='nope'), 'start date'), (dict(time='25:99'), 'start date'),
                             (dict(message='m' * 201), '200')):
            _s, _e, _m, errors = maintenance.parse_schedule({**good, **change}, now)
            self.assertTrue(any(word in e for e in errors), (change, errors))

    def test_retry_after_is_reasonable(self):
        maintenance.start_now(10)
        self.assertTrue(30 <= maintenance.retry_after() <= 600)
        maintenance.end_now()
        self.assertEqual(maintenance.retry_after(), 60)


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
class CommandTests(WithFile, SimpleTestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('maintenance', *args, stdout=out)
        return out.getvalue()

    def test_on_status_extend_off(self):
        self.assertIn('off', self.run_cmd('status'))
        self.assertIn('Maintenance is ON', self.run_cmd('on', '--minutes', '20', '--message', 'Adding features'))
        self.assertTrue(maintenance.is_active())
        self.assertIn('ACTIVE', self.run_cmd('status'))
        self.assertIn('now ends at', self.run_cmd('extend', '--minutes', '10'))
        self.assertIn('now off', self.run_cmd('off'))
        self.assertIn('already off', self.run_cmd('off'))
        self.assertFalse(maintenance.is_active())

    def test_schedule(self):
        later = timezone.localtime(timezone.now() + timedelta(days=2))
        self.assertIn('planned', self.run_cmd('schedule', '--start', f'{later:%Y-%m-%d} 23:00', '--minutes', '30').lower())
        self.assertEqual(maintenance.state()[0], 'scheduled')

    def test_mistakes_are_explained_and_change_nothing(self):
        for args in (('on', '--minutes', '2'), ('on', '--minutes', 'abc'), ('schedule',), ('schedule', '--start', '2020-01-01 10:00'),
                     ('schedule', '--start', 'tomorrow'), ('extend',), ('on', '--message', 'x' * 300)):
            with self.assertRaises(CommandError, msg=args):
                self.run_cmd(*args)
        self.assertEqual(maintenance.state()[0], 'off')


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(**SAFE)
class PlatformPageTests(WithFile, TransactionTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        connection.set_schema_to_public()
        cache.clear()
        self.boss = get_user_model().objects.create_superuser('boss-maint', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.boss)

    def test_the_page_opens_for_the_platform_owner_only(self):
        from django.contrib.auth import get_user_model
        self.assertContains(self.client.get('/superadmin/maintenance/'), 'No maintenance planned')
        self.client.force_login(get_user_model().objects.create_user('plain-maint', 'p@example.com', 'pw-12345-xx'))
        self.assertEqual(self.client.get('/superadmin/maintenance/').status_code, 404)
        self.assertEqual(self.client.post('/superadmin/maintenance/', {'action': 'end'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get('/superadmin/maintenance/').status_code, 302)

    def test_planning_a_window_shows_it_and_a_wrong_form_changes_nothing(self):
        local = timezone.localtime(timezone.now() + timedelta(days=1))
        form = dict(action='schedule', date=f'{local:%Y-%m-%d}', time='23:00', minutes='30', message='New features')
        resp = self.client.post('/superadmin/maintenance/', form, follow=True)
        self.assertContains(resp, 'Maintenance planned')
        self.assertEqual(maintenance.state()[0], 'scheduled')
        self.assertContains(self.client.get('/superadmin/maintenance/'), 'New features')
        self.assertContains(self.client.get('/superadmin/'), 'Maintenance is planned')
        old = maintenance.window()
        resp = self.client.post('/superadmin/maintenance/', {**form, 'minutes': '1'}, follow=True)
        self.assertContains(resp, 'between')
        self.assertEqual(maintenance.window()['end'], old['end'])
        self.client.post('/superadmin/maintenance/', {'action': 'end'})
        self.assertEqual(maintenance.state()[0], 'off')

    def test_start_now_needs_the_tick_and_the_owner_keeps_working_during_it(self):
        resp = self.client.post('/superadmin/maintenance/', dict(action='start', minutes='15'), follow=True)
        self.assertContains(resp, 'Tick the box')
        self.assertFalse(maintenance.is_active())
        self.client.post('/superadmin/maintenance/', dict(action='start', minutes='15', sure='1', message='Quick update'))
        self.assertTrue(maintenance.is_active())
        self.assertContains(self.client.get('/superadmin/'), 'Maintenance is ON')                  # the platform pages still open
        self.assertEqual(self.client.get('/superadmin/maintenance/').status_code, 200)
        resp = self.client.post('/superadmin/maintenance/', dict(action='extend', minutes='10'), follow=True)
        self.assertContains(resp, 'now ends at')
        resp = self.client.post('/superadmin/maintenance/', dict(action='end'), follow=True)
        self.assertContains(resp, 'Shops open again')
        self.assertFalse(maintenance.is_active())

    def test_the_notice_e_mail_goes_to_each_owner_once_and_a_failure_is_counted(self):
        shops = [SimpleNamespace(owner_name='Asha', name='Asha Medical', email='asha@example.com', schema_name='s_a'),
                 SimpleNamespace(owner_name='Bina', name='Bina Chemist', email='bina@example.com', schema_name='s_b')]
        now = timezone.now()
        window = dict(start=now + timedelta(hours=1), end=now + timedelta(hours=2), message='New features', emailed=False)
        with mock.patch.object(maintenance_admin, 'owners', return_value=shops):
            self.assertEqual(maintenance_admin.email_owners(window), (2, 0, 0))
            self.assertEqual([m.to for m in mail.outbox], [['asha@example.com'], ['bina@example.com']])
            first = mail.outbox[0]
            self.assertIn('Planned maintenance', first.subject)
            self.assertIn('Hello Asha', first.body)
            self.assertIn('Asha Medical', first.body)
            self.assertIn('New features', first.body)
            self.assertEqual(first.alternatives[0][1], 'text/html')
            mail.outbox.clear()
            real = maintenance_admin.mails.send
            calls = []

            def flaky(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise OSError('smtp down')
                return real(*args, **kwargs)
            with mock.patch.object(maintenance_admin.mails, 'send', side_effect=flaky):
                self.assertEqual(maintenance_admin.email_owners(window), (1, 1, 0))
            self.assertEqual(maintenance_admin.email_owners(window, budget=-1), (0, 0, 2))      # out of time: nothing is half-sent

    def test_the_e_mail_while_running_says_when_the_shop_is_back(self):
        from tenants import mails
        now = timezone.now()
        shop = SimpleNamespace(owner_name='Asha', name='Asha Medical', email='a@example.com', schema_name='s_a')
        window = dict(start=now - timedelta(minutes=1), end=now + timedelta(minutes=30), message='', emailed=False)
        subject, text, html = mails.maintenance_message(shop, window, now)
        self.assertIn('being updated', subject)
        self.assertIn('be back by', text)
        self.assertNotIn('save your work', text)

    def test_the_schedule_form_can_e_mail_everybody(self):
        shops = [SimpleNamespace(owner_name='Asha', name='Asha Medical', email='asha@example.com', schema_name='s_a')]
        local = timezone.localtime(timezone.now() + timedelta(days=1))
        form = dict(action='schedule', date=f'{local:%Y-%m-%d}', time='23:00', minutes='30', email='1')
        with mock.patch.object(maintenance_admin, 'owners', return_value=shops):
            resp = self.client.post('/superadmin/maintenance/', form, follow=True)
        self.assertContains(resp, 'E-mail sent to 1 shop owner')
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(maintenance.window()['emailed'])


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(**SAFE)
class ShopDuringMaintenanceTests(WithFile, TransactionTestCase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        connection.set_schema_to_public()
        cache.clear()
        self.addCleanup(self.drop_schema)
        self.boss = get_user_model().objects.create_superuser('boss-down', 'b@example.com', 'pw-12345-xx')

    def drop_schema(self):
        connection.set_schema_to_public()
        Shop.objects.filter(schema_name=SCHEMA).delete()
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')

    def make_shop(self):
        self.client.post('/signup/', GOOD)
        link = [x for x in mail.outbox[-1].body.splitlines() if '/signup/confirm/' in x][0].strip()
        self.client.post('/' + link.split('/', 3)[3])
        self.assertTrue(Shop.objects.filter(schema_name=SCHEMA).exists())

    def test_shops_show_the_update_page_and_everything_else_keeps_working(self):
        self.make_shop()
        self.assertEqual(self.client.get('/login/', HTTP_HOST=HOST).status_code, 200)
        maintenance.start_now(15, 'Adding new features')
        for method in (self.client.get, self.client.post):
            resp = method('/login/', HTTP_HOST=HOST)
            self.assertEqual(resp.status_code, 503)
            self.assertContains(resp, 'We are updating MedSync', status_code=503)
            self.assertContains(resp, 'Adding new features', status_code=503)
            self.assertContains(resp, 'Your shop and all its data are safe', status_code=503)
            self.assertTrue(30 <= int(resp['Retry-After']) <= 3600)
            self.assertEqual(resp['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/', HTTP_HOST=HOST).status_code, 503)
        self.assertEqual(self.client.get('/healthz/', HTTP_HOST=HOST).status_code, 200)           # the uptime monitor still sees "ok"
        connection.set_schema_to_public()                                                         # the last request ran inside the shop
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get('/superadmin/').status_code, 200)                        # the platform owner keeps working
        maintenance.end_now()
        self.assertEqual(self.client.get('/login/', HTTP_HOST=HOST).status_code, 200)

    def test_a_planned_window_does_not_stop_the_shops_before_it_starts(self):
        self.make_shop()
        now = timezone.now()
        maintenance.schedule(now + timedelta(hours=1), now + timedelta(hours=2), '')
        self.assertEqual(self.client.get('/login/', HTTP_HOST=HOST).status_code, 200)
        maintenance.schedule(now - timedelta(hours=2), now - timedelta(hours=1), '')              # a window that is over
        self.assertEqual(self.client.get('/login/', HTTP_HOST=HOST).status_code, 200)

    def test_new_shops_are_not_made_while_updating_and_the_link_stays_valid(self):
        self.client.post('/signup/', GOOD)
        link = [x for x in mail.outbox[-1].body.splitlines() if '/signup/confirm/' in x][0].strip()
        path = '/' + link.split('/', 3)[3]
        maintenance.start_now(15)
        resp = self.client.post(path)
        self.assertContains(resp, 'Sign-up is paused', status_code=503)
        self.assertFalse(Shop.objects.filter(schema_name=SCHEMA).exists())
        self.assertEqual(self.client.post('/signup/', dict(GOOD, subdomain='other-test', email='other@example.com')).status_code, 503)
        maintenance.end_now()
        self.client.post(path)
        self.assertTrue(Shop.objects.filter(schema_name=SCHEMA).exists())

    def test_a_banner_is_in_the_shop_pages_template(self):
        with open(os.path.join(settings.BASE_DIR, 'templates', 'app', 'base.html'), encoding='utf-8') as f:
            self.assertIn('{% if maintenance_banner %}', f.read())
