"""Sign-up, shop creation and the trial lock. Runs only with MULTI_SHOP=1 (PostgreSQL), because each shop is a schema."""
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

if settings.MULTI_SHOP:
    from tenants.models import Domain, PendingSignup, Shop

GOOD = dict(shop_name='Sharma Medical', owner_name='Raj Sharma', email='raj@example.com', phone='98765 43210',
            subdomain='sharma-test', password='Str0ng-pass-77', password2='Str0ng-pass-77', agree='1')
SCHEMA = 's_sharma_test'
HOST = 'sharma-test.localhost'


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost', SIGNUP_SKIP_EMAIL=False,
                   SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class SignupFlowTests(TransactionTestCase):
    def setUp(self):
        cache.clear()                                       # the sign-up limit and login lock live in the cache
        self.addCleanup(self.drop_schema)

    def drop_schema(self):
        Shop.objects.filter(schema_name=SCHEMA).delete()
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')

    def sign_up(self, **changes):
        return self.client.post('/signup/', dict(GOOD, **changes))

    def confirm(self):
        link = [line for line in mail.outbox[-1].body.splitlines() if '/signup/confirm/' in line][0].strip()
        path = '/' + link.split('/', 3)[3]
        self.assertEqual(self.client.get(path).status_code, 200)            # a button only: nothing is made yet
        self.assertFalse(Shop.objects.filter(schema_name=SCHEMA).exists())
        return self.client.post(path)

    def test_the_form_opens(self):
        resp = self.client.get('/signup/')
        self.assertContains(resp, 'Create your shop')
        self.assertContains(resp, 'Rs 700 a month or Rs 8000 a year')

    def test_mistakes_are_explained_and_nothing_is_saved(self):
        for changes, field in (({'subdomain': 'admin'}, 'subdomain'), ({'subdomain': 'a b'}, 'subdomain'),
                               ({'email': 'nope'}, 'email'), ({'phone': '123'}, 'phone'), ({'password2': 'x'}, 'password2'),
                               ({'password': '12345678', 'password2': '12345678'}, 'password'), ({'agree': ''}, 'agree'),
                               ({'shop_name': ''}, 'shop_name')):
            with self.subTest(field=field, changes=changes):
                resp = self.sign_up(**changes)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(field, resp.context['errors'])
        self.assertEqual(PendingSignup.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_robot_filling_the_hidden_field_is_ignored(self):
        self.assertEqual(self.sign_up(website='http://spam').status_code, 302)
        self.assertEqual(PendingSignup.objects.count(), 0)

    def test_full_flow_makes_a_working_shop_with_a_30_day_trial(self):
        resp = self.sign_up()
        self.assertContains(resp, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['raj@example.com'])
        self.assertFalse(Shop.objects.filter(schema_name=SCHEMA).exists())      # not before the link is used
        resp = self.confirm()
        self.assertContains(resp, 'Your shop is ready')
        shop = Shop.objects.get(schema_name=SCHEMA)
        self.assertEqual((shop.plan, shop.name, shop.email_verified), ('trial', 'Sharma Medical', True))
        self.assertEqual(shop.trial_ends, timezone.localdate() + timedelta(days=30))
        self.assertTrue(Domain.objects.filter(domain=HOST, tenant=shop).exists())
        # the owner signs in on the shop's own address, and sees the shop's name
        self.assertEqual(self.client.get('/', HTTP_HOST=HOST).status_code, 302)       # anonymous: to the login
        login = self.client.post('/login/', {'username': 'raj@example.com', 'password': GOOD['password']}, HTTP_HOST=HOST)
        self.assertEqual(login.status_code, 302)
        page = self.client.get('/', HTTP_HOST=HOST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Free trial: 30 days left.')
        # a used link cannot make a second shop
        token = PendingSignup.objects.get().token
        self.assertContains(self.client.post(f'/signup/confirm/{token}/'), 'Your shop is ready')
        self.assertEqual(Shop.objects.filter(schema_name=SCHEMA).count(), 1)

    def test_the_owner_of_a_shop_is_not_a_user_of_the_main_site(self):
        self.sign_up()
        self.confirm()
        login = self.client.post('/login/', {'username': 'raj@example.com', 'password': GOOD['password']})   # main site
        self.assertEqual(login.status_code, 200)                                                             # refused
        self.assertFalse(login.wsgi_request.user.is_authenticated)

    def test_a_taken_address_or_email_is_refused(self):
        self.sign_up()
        self.confirm()
        self.assertIn('subdomain', self.sign_up(email='other@example.com').context['errors'])
        self.assertIn('email', self.sign_up(subdomain='another-one').context['errors'])

    def test_a_shop_pages_do_not_show_the_marketing_site_or_blog(self):
        self.sign_up()
        self.confirm()
        self.assertEqual(self.client.get('/blog/', HTTP_HOST=HOST).status_code, 404)
        self.assertEqual(self.client.get('/sitemap.xml', HTTP_HOST=HOST).status_code, 404)
        self.assertContains(self.client.get('/robots.txt', HTTP_HOST=HOST), 'Disallow: /')
        self.assertEqual(self.client.get('/signup/', HTTP_HOST=HOST).status_code, 404)

    def test_paused_and_locked_shops(self):
        self.sign_up()
        self.confirm()
        self.client.post('/login/', {'username': 'raj@example.com', 'password': GOOD['password']}, HTTP_HOST=HOST)
        shop = Shop.objects.get(schema_name=SCHEMA)
        Shop.objects.filter(pk=shop.pk).update(trial_ends=timezone.localdate() - timedelta(days=1))      # trial over
        self.assertEqual(self.client.get('/', HTTP_HOST=HOST).status_code, 200)                           # can still look
        self.assertContains(self.client.get('/', HTTP_HOST=HOST), 'subscription has ended')
        self.assertEqual(self.client.post('/logout/', HTTP_HOST=HOST).status_code, 302)                  # and sign out
        self.client.post('/login/', {'username': 'raj@example.com', 'password': GOOD['password']}, HTTP_HOST=HOST)
        self.assertEqual(self.client.post('/products/new/', {'name': 'X'}, HTTP_HOST=HOST).status_code, 403)   # but not save
        Shop.objects.filter(pk=shop.pk).update(paid_until=timezone.localdate() + timedelta(days=30), plan='monthly')
        self.assertNotEqual(self.client.post('/products/new/', {'name': 'X'}, HTTP_HOST=HOST).status_code, 403)
        Shop.objects.filter(pk=shop.pk).update(is_paused=True)
        self.assertEqual(self.client.get('/', HTTP_HOST=HOST).status_code, 403)

    def test_backups_of_one_shop_stay_inside_that_shop(self):
        from backend import backup
        from django_tenants.utils import schema_context
        self.sign_up()
        self.confirm()
        with schema_context(SCHEMA):
            self.assertEqual(backup.current_schema(), SCHEMA)
        self.assertEqual(backup.current_schema(), '')                       # the main site sees the whole database
