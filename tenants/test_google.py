"""Google sign-in: the parts that need no database (token checks, switches, buttons). The full flow with shops is in
test_google_flow.py (it needs MULTI_SHOP=1 and PostgreSQL)."""
import base64
import json
import time
from unittest import mock

from django.test import SimpleTestCase, override_settings

from tenants import google

KEYS = dict(GOOGLE_CLIENT_ID='cid.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='sec', MULTI_SHOP=True)


def make_token(**claims):
    base = dict(iss='https://accounts.google.com', aud=KEYS['GOOGLE_CLIENT_ID'], exp=time.time() + 600, nonce='N1',
                email='Raj@Example.com', email_verified=True, name='Raj  Sharma')
    base.update(claims)
    part = base64.urlsafe_b64encode(json.dumps(base).encode()).decode().rstrip('=')
    return f'xx.{part}.yy'


@override_settings(**KEYS)
class TokenChecksTests(SimpleTestCase):
    def check(self, nonce='N1', **claims):
        return google.check_claims(google.id_token_claims(make_token(**claims)), nonce)

    def test_a_good_token_gives_the_lowercase_email_and_a_tidy_name(self):
        self.assertEqual(self.check(), ('raj@example.com', 'Raj Sharma'))

    def test_the_other_issuer_spelling_is_fine(self):
        self.assertEqual(self.check(iss='accounts.google.com')[0], 'raj@example.com')

    def test_each_wrong_thing_is_refused(self):
        for bad in (dict(iss='https://evil.example'), dict(aud='someone-else'), dict(exp=time.time() - 5),
                    dict(email_verified=False), dict(email_verified='false'), dict(email=''), dict(exp='soon')):
            with self.assertRaises(google.GoogleError, msg=str(bad)):
                self.check(**bad)

    def test_a_missing_email_verified_is_refused(self):
        claims = google.id_token_claims(make_token())
        del claims['email_verified']
        with self.assertRaises(google.GoogleError):
            google.check_claims(claims, 'N1')

    def test_a_wrong_or_empty_nonce_is_refused(self):
        for nonce in ('other', '', None):
            with self.assertRaises(google.GoogleError):
                self.check(nonce=nonce)

    def test_garbage_is_not_a_token(self):
        for junk in ('', 'abc', 'a.b.c', 'a.' + base64.urlsafe_b64encode(b'[1]').decode() + '.c'):
            with self.assertRaises(google.GoogleError, msg=junk):
                google.id_token_claims(junk)


class SwitchTests(SimpleTestCase):
    def test_it_is_off_without_keys_or_without_many_shops(self):
        with override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', MULTI_SHOP=True):
            self.assertFalse(google.configured())
        with override_settings(GOOGLE_CLIENT_ID='a', GOOGLE_CLIENT_SECRET='b', MULTI_SHOP=False):
            self.assertFalse(google.configured())
        with override_settings(GOOGLE_CLIENT_ID='a', GOOGLE_CLIENT_SECRET='', MULTI_SHOP=True):
            self.assertFalse(google.configured())
        with override_settings(**KEYS):
            self.assertTrue(google.configured())

    def test_the_pages_are_404_while_it_is_off(self):
        with override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver']):
            for path in ('/auth/google/', '/auth/google/callback/'):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_the_signup_session_data_expires(self):
        class Req:
            session = {google.SIGNUP_SESSION_KEY: dict(email='a@b.co', name='A', at=time.time() - google.SIGNUP_VALID_SECONDS - 5)}
        self.assertIsNone(google.signup_data(Req))
        self.assertNotIn(google.SIGNUP_SESSION_KEY, Req.session)
        Req.session[google.SIGNUP_SESSION_KEY] = dict(email='a@b.co', name='A', at=time.time())
        self.assertEqual(google.signup_data(Req)['email'], 'a@b.co')
        Req.session[google.SIGNUP_SESSION_KEY] = 'junk'
        self.assertIsNone(google.signup_data(Req))

    def test_the_network_call_failing_is_a_polite_error(self):
        import urllib.error
        with override_settings(**KEYS), mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('down')):
            with self.assertRaises(google.GoogleError):
                google.exchange_code('c', 'https://x/auth/google/callback/')


@override_settings(**KEYS, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ViewStepsTests(SimpleTestCase):
    """start and callback called directly (no shop database needed): the state, nonce and refusal steps."""

    def request(self, path, **params):
        from django.contrib.sessions.backends.signed_cookies import SessionStore
        from django.test import RequestFactory
        req = RequestFactory().get(path, params)
        req.session = SessionStore()
        from django.contrib.auth.models import AnonymousUser
        req.user = AnonymousUser()
        return req

    def test_start_redirects_to_google_and_remembers_state_and_nonce(self):
        req = self.request('/auth/google/', mode='signup')
        r = google.start(req)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(google.AUTH_URL + '?'))
        saved = req.session['google_oauth']
        self.assertEqual(saved['mode'], 'signup')
        self.assertIn('state=' + saved['state'], r['Location'])
        self.assertIn('nonce=' + saved['nonce'], r['Location'])
        self.assertNotEqual(saved['state'], saved['nonce'])

    def test_an_unknown_mode_means_login(self):
        req = self.request('/auth/google/', mode='<script>')
        google.start(req)
        self.assertEqual(req.session['google_oauth']['mode'], 'login')

    def started(self, mode='login'):
        req = self.request('/auth/google/', mode=mode)
        google.start(req)
        return req.session

    def call_back(self, session, **params):
        req = self.request('/auth/google/callback/', **params)
        req.session = session
        req.META['REMOTE_ADDR'] = '203.0.113.9'
        return google.callback(req)

    def test_forged_state_missing_code_and_cancel_are_refused_and_the_start_is_used_up(self):
        from django.core.cache import cache
        cache.clear()
        for params in (dict(state='forged', code='c'), dict(code='c'), dict(error='access_denied')):
            session = self.started()
            with mock.patch('tenants.google.exchange_code') as ex:
                r = self.call_back(session, **params)
            self.assertEqual(r.status_code, 400, params)
            ex.assert_not_called()
            self.assertNotIn('google_oauth', session)

    def test_a_callback_without_a_start_is_refused(self):
        from django.core.cache import cache
        cache.clear()
        from django.contrib.sessions.backends.signed_cookies import SessionStore
        self.assertEqual(self.call_back(SessionStore(), state='x', code='c').status_code, 400)

    def test_a_good_answer_goes_to_the_right_step_for_the_mode(self):
        from django.core.cache import cache
        cache.clear()
        for mode, step in (('login', '_sign_in'), ('signup', '_sign_up')):
            session = self.started(mode)
            saved = session['google_oauth']
            token = make_token(nonce=saved['nonce'])
            with mock.patch('tenants.google.exchange_code', return_value={'id_token': token}), \
                    mock.patch(f'tenants.google.{step}', return_value='STEP') as go:
                r = self.call_back(session, state=saved['state'], code='c')
            self.assertEqual(r, 'STEP')
            go.assert_called_once()
            self.assertEqual(go.call_args.args[1], 'raj@example.com')

    def test_a_token_for_another_visit_or_unverified_email_is_refused(self):
        from django.core.cache import cache
        for bad in (dict(nonce='not-ours'), dict(email_verified=False)):
            cache.clear()
            session = self.started()
            saved = session['google_oauth']
            kw = dict(dict(nonce=saved['nonce']), **bad)
            with mock.patch('tenants.google.exchange_code', return_value={'id_token': make_token(**kw)}), \
                    mock.patch('tenants.google._sign_in') as go:
                r = self.call_back(session, state=saved['state'], code='c')
            self.assertEqual(r.status_code, 400, bad)
            go.assert_not_called()

    def test_too_many_tries_from_one_address_are_stopped(self):
        from django.core.cache import cache
        cache.clear()
        cache.set('google-ip:203.0.113.9', google.MAX_TRIES, 600)
        self.assertEqual(self.call_back(self.started(), state='x', code='c').status_code, 429)
