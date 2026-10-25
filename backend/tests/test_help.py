"""Help page, 'get your shop ready' card, the uptime check, error e-mail privacy and the update script."""
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from backend import onboarding
from backend.errorfilter import HidePrivateRequestData
from backend.models import Bill, ShopProfile
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class HealthCheckTests(TestCase):
    def test_the_uptime_check_needs_no_login_and_says_ok(self):
        resp = self.client.get('/healthz/')
        self.assertEqual((resp.status_code, resp.json()), (200, {'status': 'ok'}))
        self.assertIn('no-cache', resp['Cache-Control'])

    def test_it_says_503_when_the_database_does_not_answer(self):
        with mock.patch('backend.helpviews.connection') as conn:
            conn.cursor.side_effect = RuntimeError('database down')
            resp = self.client.get('/healthz/')
        self.assertEqual((resp.status_code, resp.json()), (503, {'status': 'down'}))

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post('/healthz/').status_code, 405)


@override_settings(**SAFE)
class HelpPageTests(TestCase):
    def test_help_needs_a_sign_in_and_every_user_can_open_it(self):
        self.assertEqual(self.client.get('/help/').status_code, 302)
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345-xx')        # no permissions at all
        self.client.force_login(clerk)
        resp = self.client.get('/help/')
        self.assertContains(resp, 'Help and support')
        self.assertContains(resp, 'How do I make a bill?')
        self.assertContains(resp, 'href="/terms/"')

    def test_the_menu_has_the_help_entry(self):
        owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(owner)
        self.assertContains(self.client.get('/'), 'href="/help/"')

    @override_settings(LANDING_WHATSAPP='919876543210')
    def test_the_whatsapp_button_uses_the_configured_number(self):
        self.client.force_login(get_user_model().objects.create_user('clerk', password='pw-12345-xx'))
        self.assertContains(self.client.get('/help/'), 'wa.me/919876543210')


@override_settings(**SAFE)
class SetupCardTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')

    def done(self):
        return {i['key']: i['done'] for i in onboarding.steps()}

    def test_the_steps_tick_themselves(self):
        # StockMixin already made a product
        self.assertEqual(self.done(), {'shop': False, 'products': True, 'stock': False, 'bill': False})
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Shop', address='1 Main Road', phone='9876543210')
        self.buy()
        self.assertEqual(self.done(), {'shop': True, 'products': True, 'stock': True, 'bill': False})
        bill = self.new_bill()
        Bill.objects.filter(pk=bill.pk).update(status='FINAL')
        self.assertTrue(self.done()['bill'])

    def test_the_owner_sees_the_card_until_everything_is_done(self):
        self.client.force_login(self.owner)
        page = self.client.get('/')
        self.assertContains(page, 'Get your shop ready')
        self.assertContains(page, '1 of 4 steps done')
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Shop', address='1 Main Road', phone='9876543210')
        self.buy()
        bill = self.new_bill()
        Bill.objects.filter(pk=bill.pk).update(status='FINAL')
        self.assertNotContains(self.client.get('/'), 'Get your shop ready')

    def test_hide_for_now_hides_it_and_other_users_never_see_it(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/help/hide-setup/').status_code, 405)
        self.assertEqual(self.client.post('/help/hide-setup/').status_code, 302)
        self.assertNotContains(self.client.get('/'), 'Get your shop ready')
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345-xx')
        self.assertIsNone(onboarding.checklist(clerk))

    def test_a_problem_while_working_it_out_never_breaks_the_dashboard(self):
        with mock.patch('backend.onboarding.steps', side_effect=RuntimeError('boom')):
            self.assertIsNone(onboarding.checklist(self.owner))


class ErrorMailPrivacyTests(SimpleTestCase):
    def test_form_values_and_cookies_never_go_into_an_error_mail(self):
        request = RequestFactory().post('/login/', {'username': 'raj@example.com', 'password': 'Secret-123'})
        request.COOKIES = {'sessionid': 'abc123'}
        f = HidePrivateRequestData()
        self.assertEqual(f.get_post_parameters(request), {'username': '********', 'password': '********'})
        self.assertEqual(f.get_safe_cookies(request), {'sessionid': '********'})
        self.assertEqual(f.get_post_parameters(None), {})

    def test_the_filter_is_the_one_in_use(self):
        from django.conf import settings
        self.assertEqual(settings.DEFAULT_EXCEPTION_REPORTER_FILTER, 'backend.errorfilter.HidePrivateRequestData')


class UpdateScriptTests(SimpleTestCase):
    SCRIPT = Path(__file__).resolve().parents[2] / 'deploy.sh'

    def test_the_script_exists_is_executable_and_valid(self):
        self.assertTrue(self.SCRIPT.exists())
        git = shutil.which('git')                      # Windows files have no executable bit, so ask git what the server will get
        if git:
            out = subprocess.run([git, 'ls-files', '--stage', '--', self.SCRIPT.name], capture_output=True, text=True,
                                 cwd=str(self.SCRIPT.parent)).stdout
            if out.strip():
                self.assertTrue(out.startswith('100755'), 'deploy.sh must be committed as executable: ' + out)
        bash = shutil.which('bash')
        if bash:
            done = subprocess.run([bash, '-n', str(self.SCRIPT)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_it_stops_at_the_first_problem(self):
        self.assertIn('set -euo pipefail', self.SCRIPT.read_text())
