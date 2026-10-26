"""2-step sign-in: a code by e-mail after the password. Opt-in, with recovery codes, so nobody is locked out."""
import re
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from backend import twofactor
from backend.models import TwoFactor
from backend.models_extra import AuditLog

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
PW = 'pw-12345-xx'


def last_code():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


@override_settings(**SAFE)
class TwoStepBase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_superuser('boss', 'boss@example.com', PW)

    def turn_on(self, user=None):
        """Turns it on the way a person does (through the page). Returns the recovery codes."""
        user = user or self.owner
        c = self.client_class()
        c.force_login(user)
        c.post('/security/', {'action': 'send'})
        resp = c.post('/security/', {'action': 'confirm', 'code': last_code()})
        return resp.context['new_codes']

    def sign_in(self, client=None, **extra):
        return (client or self.client).post('/login/', dict(username='boss', password=PW, **extra))

    def signed_in(self, client=None):
        return (client or self.client).get('/help/').status_code == 200


class TurnOnTests(TwoStepBase):
    def test_nothing_changes_for_people_who_do_not_turn_it_on(self):
        resp = self.sign_in()
        self.assertEqual((resp.status_code, resp['Location']), (302, '/'))
        self.assertTrue(self.signed_in())

    def test_the_owner_turns_it_on_with_a_code_and_gets_recovery_codes(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/security/'), 'Turn on 2-step sign-in')
        self.client.post('/security/', {'action': 'send'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['boss@example.com'])
        wrong = '000000' if last_code() != '000000' else '111111'
        resp = self.client.post('/security/', {'action': 'confirm', 'code': wrong}, follow=True)
        self.assertContains(resp, 'not right')
        self.assertFalse(TwoFactor.objects.exists())
        resp = self.client.post('/security/', {'action': 'confirm', 'code': last_code()})
        self.assertEqual(resp.status_code, 200)
        codes = resp.context['new_codes']
        self.assertEqual(len(codes), 8)
        self.assertEqual(len(set(codes)), 8)
        self.assertTrue(TwoFactor.objects.filter(user=self.owner).exists())
        stored = TwoFactor.objects.get(user=self.owner).recovery
        self.assertEqual(len(stored), 8)
        self.assertFalse(any(c.replace('-', '') in ' '.join(stored) for c in codes))        # only hashes are saved
        self.assertTrue(AuditLog.objects.filter(action='SECURITY', detail='Turned on').exists())
        self.assertContains(self.client.get('/security/'), '8 unused recovery codes')

    def test_without_an_email_address_it_cannot_be_turned_on(self):
        nomail = get_user_model().objects.create_user('clerk', password=PW)
        self.client.force_login(nomail)
        self.assertContains(self.client.get('/security/'), 'no e-mail address')
        self.client.post('/security/', {'action': 'send'})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(TwoFactor.objects.exists())

    def test_a_failed_email_changes_nothing(self):
        self.client.force_login(self.owner)
        with mock.patch('backend.twofactor.send_mail', side_effect=OSError('smtp down')):
            resp = self.client.post('/security/', {'action': 'send'}, follow=True)
        self.assertContains(resp, 'Nothing was changed')
        self.assertFalse(TwoFactor.objects.exists())

    def test_the_security_page_needs_a_sign_in(self):
        resp = self.client.get('/security/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_the_menu_has_the_entry(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/help/'), 'href="/security/"')

    def test_turning_off_and_new_recovery_codes_need_the_password(self):
        old = self.turn_on()
        self.client.force_login(self.owner)
        resp = self.client.post('/security/', {'action': 'disable', 'password': 'wrong'}, follow=True)
        self.assertContains(resp, 'password is not right')
        self.assertTrue(TwoFactor.objects.exists())
        resp = self.client.post('/security/', {'action': 'codes', 'password': PW})
        fresh = resp.context['new_codes']
        self.assertEqual(len(fresh), 8)
        self.assertFalse(set(fresh) & set(old))
        self.client.post('/security/', {'action': 'disable', 'password': PW})
        self.assertFalse(TwoFactor.objects.exists())
        self.client.logout()
        self.assertEqual(self.sign_in()['Location'], '/')


class SignInTests(TwoStepBase):
    def setUp(self):
        super().setUp()
        self.recovery = self.turn_on()
        mail.outbox.clear()

    def test_the_right_password_alone_does_not_sign_in(self):
        resp = self.sign_in()
        self.assertEqual((resp.status_code, resp['Location']), (302, '/login/verify/'))
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(self.client.get('/login/verify/'), 'b***@example.com')

    def test_the_wrong_password_is_still_refused_before_any_code(self):
        resp = self.client.post('/login/', dict(username='boss', password='nope'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_emailed_code_signs_in_and_goes_to_the_page_asked_for(self):
        self.client.post('/login/?next=/bills/', dict(username='boss', password=PW, next='/bills/'))
        resp = self.client.post('/login/verify/', {'code': last_code(), 'action': 'verify'})
        self.assertEqual((resp.status_code, resp['Location']), (302, '/bills/'))
        self.assertTrue(self.signed_in())
        self.assertTrue(AuditLog.objects.filter(action='LOGIN', user='boss').exists())

    def test_a_wrong_code_is_refused_and_five_wrong_codes_end_the_attempt(self):
        self.sign_in()
        wrong = '000000' if last_code() != '000000' else '111111'
        for i in range(4):
            self.assertContains(self.client.post('/login/verify/', {'code': wrong, 'action': 'verify'}), 'not right')
        resp = self.client.post('/login/verify/', {'code': wrong, 'action': 'verify'})
        self.assertEqual((resp.status_code, resp['Location']), (302, '/login/'))
        self.assertFalse(self.signed_in())
        resp = self.client.post('/login/verify/', {'code': last_code(), 'action': 'verify'})   # the attempt is over
        self.assertEqual(resp['Location'], '/login/')
        self.assertTrue(AuditLog.objects.filter(action='LOGIN FAILED', detail='Wrong 2-step code').exists())

    def test_an_expired_code_does_not_work(self):
        self.sign_in()
        code = last_code()
        s = self.client.session
        s['pre2fa']['exp'] = 0
        s.save()
        self.assertContains(self.client.post('/login/verify/', {'code': code, 'action': 'verify'}), 'expired')
        self.assertFalse(self.signed_in())

    def test_a_code_from_another_sign_in_does_not_work(self):
        self.sign_in()
        first = last_code()
        c2 = self.client_class()
        self.sign_in(c2)
        second = last_code()
        if first != second:
            self.assertContains(c2.post('/login/verify/', {'code': first, 'action': 'verify'}), 'not right')

    def test_a_recovery_code_works_once(self):
        self.sign_in()
        code = self.recovery[0].lower().replace('-', ' ')               # typed in small letters with a space
        resp = self.client.post('/login/verify/', {'code': code, 'action': 'verify'})
        self.assertEqual(resp['Location'], '/')
        self.assertTrue(self.signed_in())
        self.assertEqual(len(TwoFactor.objects.get().recovery), 7)
        self.client.logout()
        self.sign_in()
        self.assertContains(self.client.post('/login/verify/', {'code': self.recovery[0], 'action': 'verify'}), 'not right')
        resp = self.client.post('/login/verify/', {'code': self.recovery[1], 'action': 'verify'})
        self.assertEqual(resp['Location'], '/')

    def test_when_the_email_cannot_be_sent_a_recovery_code_still_gets_the_person_in(self):
        with mock.patch('backend.twofactor.send_mail', side_effect=OSError('smtp down')):
            resp = self.sign_in()
        self.assertEqual(resp['Location'], '/login/verify/')
        page = self.client.get('/login/verify/')
        self.assertContains(page, 'recovery code')
        resp = self.client.post('/login/verify/', {'code': self.recovery[2], 'action': 'verify'})
        self.assertEqual(resp['Location'], '/')

    def test_a_new_code_can_be_asked_for_but_not_at_once_and_not_endlessly(self):
        self.sign_in()
        self.assertContains(self.client.post('/login/verify/', {'action': 'resend'}), 'Please wait')
        self.assertEqual(len(mail.outbox), 1)
        for _ in range(twofactor.MAX_SENDS - 1):
            s = self.client.session
            s['pre2fa']['last'] = 0                                      # as if the wait had passed
            s.save()
            self.client.post('/login/verify/', {'action': 'resend'})
        self.assertEqual(len(mail.outbox), twofactor.MAX_SENDS)
        s = self.client.session
        s['pre2fa']['last'] = 0
        s.save()
        self.assertContains(self.client.post('/login/verify/', {'action': 'resend'}), 'too many codes')
        self.assertEqual(len(mail.outbox), twofactor.MAX_SENDS)

    def test_the_verify_page_without_a_started_sign_in_goes_back_to_login(self):
        self.assertEqual(self.client.get('/login/verify/')['Location'], '/login/')
        self.assertEqual(self.client.post('/login/verify/', {'code': '123456'})['Location'], '/login/')

    def test_remember_this_browser_skips_the_code_until_the_password_changes(self):
        self.sign_in()
        resp = self.client.post('/login/verify/', {'code': last_code(), 'action': 'verify', 'remember': '1'})
        self.assertIn('ms_td', resp.cookies)
        self.assertTrue(resp.cookies['ms_td']['httponly'])
        self.client.logout()
        mail.outbox.clear()
        resp = self.sign_in()
        self.assertEqual(resp['Location'], '/')
        self.assertTrue(self.signed_in())
        self.assertEqual(len(mail.outbox), 0)
        self.owner.set_password('Another-pass-91')
        self.owner.save()
        self.client.logout()
        resp = self.client.post('/login/', dict(username='boss', password='Another-pass-91'))
        self.assertEqual(resp['Location'], '/login/verify/')

    def test_a_remembered_browser_of_somebody_else_does_not_count(self):
        other = get_user_model().objects.create_user('other', 'other@example.com', PW)
        self.turn_on(other)
        self.sign_in()
        resp = self.client.post('/login/verify/', {'code': last_code(), 'action': 'verify', 'remember': '1'})
        self.client.logout()
        resp = self.client.post('/login/', dict(username='other', password=PW))
        self.assertEqual(resp['Location'], '/login/verify/')

    def test_without_remember_the_code_is_asked_every_time(self):
        self.sign_in()
        self.client.post('/login/verify/', {'code': last_code(), 'action': 'verify'})
        self.client.logout()
        self.assertEqual(self.sign_in()['Location'], '/login/verify/')

    def test_the_admin_panel_cannot_be_used_to_skip_the_code(self):
        resp = self.client.get('/admin/login/')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('/login/?next='))
        resp = self.client.post('/admin/login/', dict(username='boss', password=PW, next='/admin/'))
        self.assertFalse(self.client.get('/admin/').status_code == 200)
        self.assertFalse(self.signed_in())

    def test_a_broken_setting_table_never_locks_anybody_out(self):
        with mock.patch('backend.models.TwoFactor.objects') as objs:
            objs.filter.side_effect = RuntimeError('table missing')
            resp = self.sign_in()
        self.assertEqual(resp['Location'], '/')

    def test_the_owner_can_remove_a_users_2step_in_the_admin_panel(self):
        self.client.force_login(self.owner)
        row = TwoFactor.objects.get(user=self.owner)
        self.assertEqual(self.client.get('/admin/backend/twofactor/').status_code, 200)
        self.assertEqual(self.client.get('/admin/backend/twofactor/add/').status_code, 403)
        resp = self.client.post(f'/admin/backend/twofactor/{row.pk}/delete/', {'post': 'yes'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TwoFactor.objects.exists())

    def test_the_server_command_switches_it_off(self):
        out = StringIO()
        call_command('two_step_off', 'boss', stdout=out)
        self.assertIn('now off', out.getvalue())
        self.assertFalse(TwoFactor.objects.exists())
        out = StringIO()
        call_command('two_step_off', 'boss', stdout=out)
        self.assertIn('already off', out.getvalue())

    def test_the_data_export_never_contains_the_recovery_hashes(self):
        import io
        import zipfile
        self.client.force_login(self.owner)
        resp = self.client.post('/backup/export/')
        self.assertNotIn('twofactor.csv', zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


class CodeHelperTests(TestCase):
    def test_codes_have_six_digits_and_recovery_codes_are_readable(self):
        for _ in range(50):
            self.assertRegex(twofactor.new_code(), r'^\d{6}$')
        pairs = twofactor.new_recovery_codes()
        self.assertEqual(len(pairs), 8)
        for shown, raw in pairs:
            self.assertRegex(shown, r'^[A-Z2-9]{4}-[A-Z2-9]{4}$')
            self.assertNotRegex(shown, r'[01OIL]')
            self.assertEqual(twofactor.clean_recovery(shown), raw)
