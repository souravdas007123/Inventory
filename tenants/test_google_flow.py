"""Google sign-in and sign-up with real shops. Runs only with MULTI_SHOP=1 (PostgreSQL); Google itself is replaced by a stub."""
from unittest import mock, skipUnless

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings

if settings.MULTI_SHOP:
    from tenants.models import PendingSignup, Shop

from tenants.test_google import make_token

SCHEMA = 's_sharma_test'
HOST = 'sharma-test.localhost'
FORM = dict(shop_name='Sharma Medical', owner_name='Raj Sharma', phone='98765 43210', subdomain='sharma-test', agree='1')
KEYS = dict(GOOGLE_CLIENT_ID='cid.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='sec')


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost', SIGNUP_SKIP_EMAIL=False,
                   SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, **KEYS)
class GoogleFlowTests(TransactionTestCase):
    def setUp(self):
        connection.set_schema_to_public()
        cache.clear()
        self.addCleanup(self.drop_schema)

    def drop_schema(self):
        connection.set_schema_to_public()
        Shop.objects.filter(schema_name=SCHEMA).delete()
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')

    def via_google(self, mode, email='raj@example.com', verified=True, state=None, **claims):
        """Goes through Google (a stub) and returns the response of our callback page."""
        self.client.get(f'/auth/google/?mode={mode}')
        saved = self.client.session['google_oauth']
        token = make_token(nonce=saved['nonce'], email=email, email_verified=verified, **claims)
        with mock.patch('tenants.google.exchange_code', return_value={'id_token': token}):
            return self.client.get('/auth/google/callback/', {'state': state or saved['state'], 'code': 'abc'})

    def make_shop_with_password(self):
        self.client.post('/signup/', dict(FORM, email='raj@example.com', password='Str0ng-pass-77', password2='Str0ng-pass-77'))
        link = [x for x in mail.outbox[-1].body.splitlines() if '/signup/confirm/' in x][0].strip()
        self.client.post('/' + link.split('/', 3)[3])
        self.assertTrue(Shop.objects.filter(schema_name=SCHEMA).exists())

    # ---- buttons and the start page
    def test_the_buttons_show_only_when_google_is_set_up(self):
        self.assertContains(self.client.get('/login/'), '/auth/google/?mode=login')
        self.assertContains(self.client.get('/signup/'), '/auth/google/?mode=signup')
        with override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET=''):
            self.assertNotContains(self.client.get('/login/'), '/auth/google/')
            self.assertNotContains(self.client.get('/signup/'), '/auth/google/')
            self.assertEqual(self.client.get('/auth/google/').status_code, 404)

    def test_a_shops_own_address_has_no_google_pages(self):
        self.make_shop_with_password()
        self.assertEqual(self.client.get('/auth/google/', HTTP_HOST=HOST).status_code, 404)
        self.assertEqual(self.client.get('/auth/google/callback/', HTTP_HOST=HOST).status_code, 404)

    def test_start_sends_the_visitor_to_google_with_state_and_nonce(self):
        r = self.client.get('/auth/google/?mode=login')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
        saved = self.client.session['google_oauth']
        for part in (f"state={saved['state']}", f"nonce={saved['nonce']}", 'client_id=cid.apps.googleusercontent.com',
                     'redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgoogle%2Fcallback%2F', 'scope=openid+email+profile'):
            self.assertIn(part, r['Location'])

    # ---- refused callbacks
    def test_a_wrong_state_or_no_start_or_a_cancel_is_refused(self):
        self.make_shop_with_password()
        r = self.via_google('login', state='forged')
        self.assertEqual(r.status_code, 400)
        r = self.client.get('/auth/google/callback/', {'state': 'x', 'code': 'abc'})              # nothing was started
        self.assertEqual(r.status_code, 400)
        self.client.get('/auth/google/?mode=login')
        self.assertEqual(self.client.get('/auth/google/callback/', {'error': 'access_denied'}).status_code, 400)

    def test_an_unverified_google_email_is_refused(self):
        self.make_shop_with_password()
        self.assertEqual(self.via_google('login', verified=False).status_code, 400)

    def test_the_state_cannot_be_used_twice(self):
        self.make_shop_with_password()
        self.client.get('/auth/google/?mode=login')
        saved = self.client.session['google_oauth']
        token = make_token(nonce=saved['nonce'], email='raj@example.com')
        with mock.patch('tenants.google.exchange_code', return_value={'id_token': token}):
            first = self.client.get('/auth/google/callback/', {'state': saved['state'], 'code': 'abc'})
            second = self.client.get('/auth/google/callback/', {'state': saved['state'], 'code': 'abc'})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)

    # ---- sign in
    def test_the_owner_signs_in_with_google_and_lands_in_his_shop(self):
        self.make_shop_with_password()
        r = self.via_google('login', email='Raj@Example.com')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(f'http://{HOST}/sso/'), r['Location'])
        r = self.client.get('/' + r['Location'].split('/', 3)[3], HTTP_HOST=HOST)
        self.assertEqual(r.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_an_email_without_a_shop_gets_a_clear_message_and_no_session(self):
        self.make_shop_with_password()
        r = self.via_google('login', email='stranger@example.com')
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'No shop found for this Google account', status_code=404)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_the_one_time_pass_works_once(self):
        self.make_shop_with_password()
        path = '/' + self.via_google('login')['Location'].split('/', 3)[3]
        self.assertEqual(self.client.get(path, HTTP_HOST=HOST).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(path, HTTP_HOST=HOST).status_code, 400)

    # ---- sign up
    def test_signup_with_google_makes_the_shop_and_signs_him_in(self):
        r = self.via_google('signup', email='raj@example.com', name='Raj Sharma')
        self.assertEqual((r.status_code, r['Location']), (302, '/signup/'))
        form = self.client.get('/signup/')
        self.assertContains(form, 'Google confirmed')
        self.assertContains(form, 'readonly')
        self.assertNotContains(form, 'name="password"')
        self.assertEqual(PendingSignup.objects.count(), 0)
        mail.outbox.clear()
        r = self.client.post('/signup/', dict(FORM, email='someone-else@example.com'))            # a changed e-mail is ignored
        self.assertEqual(r.status_code, 302, getattr(r, 'content', b'')[:400])
        self.assertTrue(r['Location'].startswith(f'http://{HOST}/sso/'))
        shop = Shop.objects.get(schema_name=SCHEMA)
        self.assertEqual(shop.email, 'raj@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Google', mail.outbox[0].body)
        from django.contrib.auth import get_user_model
        from django_tenants.utils import schema_context
        with schema_context(SCHEMA):
            owner = get_user_model().objects.get(email='raj@example.com')
            self.assertFalse(owner.has_usable_password())
            self.assertTrue(owner.is_superuser)
        self.client.get('/' + r['Location'].split('/', 3)[3], HTTP_HOST=HOST)
        self.assertIn('_auth_user_id', self.client.session)
        # and the same Google account signs in again later
        self.client.logout()
        self.assertTrue(self.via_google('login')['Location'].startswith(f'http://{HOST}/sso/'))

    def test_a_form_mistake_keeps_the_google_email_and_makes_no_shop(self):
        self.via_google('signup')
        r = self.client.post('/signup/', dict(FORM, subdomain='x'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Google confirmed')
        self.assertFalse(Shop.objects.filter(schema_name=SCHEMA).exists())
        self.assertEqual(self.client.post('/signup/', dict(FORM)).status_code, 302)

    def test_signing_up_again_with_an_email_that_has_a_shop_says_so(self):
        self.make_shop_with_password()
        r = self.via_google('signup')
        self.assertContains(r, 'You already have a shop')
        self.assertNotIn('google_signup', self.client.session)

    def test_without_the_google_step_the_normal_form_still_needs_a_password(self):
        r = self.client.post('/signup/', dict(FORM, email='raj@example.com'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Choose a password')

    def test_the_normal_password_signup_still_works_with_google_switched_on(self):
        self.make_shop_with_password()
        r = self.client.post('/login/', {'username': 'raj@example.com', 'password': 'Str0ng-pass-77'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(f'http://{HOST}/sso/'))
