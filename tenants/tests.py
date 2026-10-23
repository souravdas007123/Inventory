"""Sign-up, shop creation and the trial lock. Runs only with MULTI_SHOP=1 (PostgreSQL), because each shop is a schema."""
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from backend import tenancy

if settings.MULTI_SHOP:
    from tenants.models import Domain, Payment, PendingSignup, Shop

GOOD = dict(shop_name='Sharma Medical', owner_name='Raj Sharma', email='raj@example.com', phone='98765 43210',
            subdomain='sharma-test', password='Str0ng-pass-77', password2='Str0ng-pass-77', agree='1')
SCHEMA = 's_sharma_test'
HOST = 'sharma-test.localhost'


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost', SIGNUP_SKIP_EMAIL=False,
                   SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class SignupFlowTests(TransactionTestCase):
    def setUp(self):
        connection.set_schema_to_public()                   # an earlier test may have left the connection inside a shop
        cache.clear()                                       # the sign-up limit and login lock live in the cache
        self.addCleanup(self.drop_schema)

    def drop_schema(self):
        connection.set_schema_to_public()
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


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost',
                   SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, PLATFORM_ADMINS=[])
class PlatformPanelTests(TransactionTestCase):
    SCHEMA = 's_panel_test'
    HOST = 'panel-test.localhost'

    def setUp(self):
        from django.contrib.auth import get_user_model
        connection.set_schema_to_public()                   # an earlier test may have left the connection inside a shop
        cache.clear()
        self.addCleanup(self.drop_schema)
        today = timezone.localdate()
        self.shop = Shop(schema_name=self.SCHEMA, name='Panel Test Shop', owner_name='Owner', email='o@example.com',
                         phone='9876543210', trial_ends=today + timedelta(days=30))
        self.shop.save()
        connection.set_schema_to_public()                   # making the shop leaves the connection inside it
        Domain.objects.create(domain=self.HOST, tenant=self.shop, is_primary=True)
        self.boss = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.boss)
        self.today = today

    def drop_schema(self):
        connection.set_schema_to_public()
        Shop.objects.filter(schema_name=self.SCHEMA).delete()
        with connection.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS {self.SCHEMA} CASCADE')

    def act(self, **data):
        resp = self.client.post(f'/superadmin/shop/{self.shop.pk}/action/', data, follow=True)
        self.shop.refresh_from_db()
        return resp, [str(m) for m in resp.context['messages']]

    def test_only_a_platform_admin_can_open_the_panel(self):
        from django.contrib.auth import get_user_model
        self.client.logout()
        resp = self.client.get('/superadmin/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])
        staff = get_user_model().objects.create_user('clerk', 'c@example.com', 'pw-12345-xx', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/superadmin/').status_code, 404)
        self.client.force_login(self.boss)
        resp = self.client.get('/superadmin/')
        self.assertContains(resp, 'Panel Test Shop')
        self.assertContains(resp, 'Free trial')

    def test_a_list_of_platform_admins_can_narrow_the_access(self):
        with override_settings(PLATFORM_ADMINS=['someone-else']):
            self.assertEqual(self.client.get('/superadmin/').status_code, 404)
        with override_settings(PLATFORM_ADMINS=['BOSS']):
            self.assertEqual(self.client.get('/superadmin/').status_code, 200)

    def test_a_shops_own_address_has_no_panel(self):
        self.assertEqual(self.client.get('/superadmin/', HTTP_HOST=self.HOST).status_code, 404)

    def test_search_and_status_filter(self):
        self.assertContains(self.client.get('/superadmin/?q=panel'), 'Panel Test Shop')
        self.assertNotContains(self.client.get('/superadmin/?q=nothing-like-this'), 'Panel Test Shop')
        self.assertNotContains(self.client.get('/superadmin/?state=locked'), 'Panel Test Shop')
        self.assertContains(self.client.get('/superadmin/?state=trial'), 'Panel Test Shop')

    def test_the_detail_page_and_the_main_site_is_not_listed(self):
        self.assertContains(self.client.get(f'/superadmin/shop/{self.shop.pk}/'), 'Mark a payment')
        public = Shop.objects.create(schema_name='public', name='Main')
        self.assertEqual(self.client.get(f'/superadmin/shop/{public.pk}/').status_code, 404)

    def test_a_monthly_payment_moves_the_paid_date_and_is_recorded(self):
        resp, msgs = self.act(action='paid', plan='monthly')
        end = tenancy.add_months(self.today, 1)
        self.assertEqual((self.shop.plan, self.shop.paid_until), ('monthly', end))
        pay = Payment.objects.get()
        self.assertEqual((pay.amount, pay.plan, pay.period_from, pay.period_to), (700, 'monthly', self.today, end))
        self.assertTrue(any('Payment of Rs 700' in m for m in msgs), msgs)
        self.assertContains(resp, 't-active')                  # the shop is now a paying one

    def test_a_second_payment_continues_from_the_old_end_date(self):
        self.act(action='paid', plan='monthly')
        first_end = self.shop.paid_until
        self.act(action='paid', plan='yearly', amount='7500', note='UPI 12345')
        self.assertEqual(self.shop.paid_until, tenancy.add_months(first_end, 12))
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(Payment.objects.filter(plan='yearly').get().note, 'UPI 12345')

    def test_wrong_entries_change_nothing(self):
        for data in (dict(action='paid', plan='weekly'), dict(action='paid', plan='monthly', amount='abc'),
                     dict(action='paid', plan='monthly', amount='0'), dict(action='trial', days='0'),
                     dict(action='trial', days='9999'), dict(action='note', note='  '), dict(action='fly')):
            with self.subTest(data=data):
                _, msgs = self.act(**data)
                self.assertEqual(len(msgs), 1)
                self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(self.shop.trial_ends, self.today + timedelta(days=30))
        self.assertEqual(self.shop.plan, 'trial')

    def test_trial_can_be_extended_only_while_on_trial(self):
        self.act(action='trial', days='10')
        self.assertEqual(self.shop.trial_ends, self.today + timedelta(days=40))
        self.act(action='paid', plan='monthly')
        _, msgs = self.act(action='trial', days='10')
        self.assertTrue(any('free trial' in m for m in msgs), msgs)

    def test_an_expired_trial_is_extended_from_today(self):
        Shop.objects.filter(pk=self.shop.pk).update(trial_ends=self.today - timedelta(days=20))
        self.act(action='trial', days='5')
        self.assertEqual(self.shop.trial_ends, self.today + timedelta(days=5))

    def test_pause_and_resume_switch_the_shop(self):
        self.act(action='pause')
        self.assertTrue(self.shop.is_paused)
        self.assertEqual(self.client.get('/login/', HTTP_HOST=self.HOST).status_code, 403)
        self.act(action='resume')
        self.assertFalse(self.shop.is_paused)
        self.assertEqual(self.client.get('/login/', HTTP_HOST=self.HOST).status_code, 200)

    def test_notes_are_kept_with_the_date(self):
        self.act(action='note', note='Called him, will pay on Monday')
        self.assertIn(f'{self.today:%Y-%m-%d}: Called him, will pay on Monday', self.shop.notes)

    def test_actions_need_post_and_a_platform_admin(self):
        self.assertEqual(self.client.get(f'/superadmin/shop/{self.shop.pk}/action/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(f'/superadmin/shop/{self.shop.pk}/action/', {'action': 'pause'}).status_code, 302)
        self.shop.refresh_from_db()
        self.assertFalse(self.shop.is_paused)


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
@override_settings(ALLOWED_HOSTS=['.localhost', 'testserver'], SHOP_BASE_DOMAIN='localhost', SECURE_SSL_REDIRECT=False,
                   SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class WebsiteSettingsTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from tenants.models import SiteSetting
        connection.set_schema_to_public()
        cache.clear()
        SiteSetting.objects.all().delete()
        self.addCleanup(lambda: (SiteSetting.objects.all().delete(), cache.clear()))
        self.boss = get_user_model().objects.create_superuser('boss-site', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.boss)

    FORM = dict(whatsapp='+91 98765-43210', contact_email='hello@example.com', announcement='Launch offer: first month free',
                price_monthly='600', price_yearly='6,500')

    def save(self, **over):
        return self.client.post('/superadmin/site/', {**self.FORM, **over}, follow=True)

    def test_the_home_page_shows_the_standard_plans_until_something_is_edited(self):
        self.client.logout()
        page = self.client.get('/').content.decode()
        self.assertIn('Rs 700', page)
        self.assertIn('Rs 8000', page)
        self.assertIn('save Rs 400', page)
        self.assertNotIn('class="ann"', page)

    def test_saved_settings_appear_on_the_home_page_and_the_sign_up_page(self):
        resp = self.save()
        self.assertContains(resp, 'Website settings saved')
        self.client.logout()
        page = self.client.get('/').content.decode()
        self.assertIn('Launch offer: first month free', page)
        self.assertIn('Rs 600', page)
        self.assertIn('Rs 6500', page)
        self.assertIn('save Rs 700', page)
        self.assertIn('wa.me/919876543210', page)
        self.assertIn('mailto:hello@example.com', page)
        self.assertIn('Rs 600 a month', self.client.get('/signup/').content.decode())

    def test_the_payment_form_uses_the_edited_price(self):
        self.save()
        shop = Shop(schema_name='s_site_price', name='Price Shop', owner_name='O', email='p@example.com',
                    trial_ends=timezone.localdate() + timedelta(days=5))
        try:
            shop.save()
            connection.set_schema_to_public()
            self.client.post(f'/superadmin/shop/{shop.pk}/action/', dict(action='paid', plan='monthly'))
            self.assertEqual(Payment.objects.get(shop=shop).amount, 600)
        finally:
            connection.set_schema_to_public()
            Shop.objects.filter(schema_name='s_site_price').delete()
            with connection.cursor() as cur:
                cur.execute('DROP SCHEMA IF EXISTS s_site_price CASCADE')

    def test_wrong_entries_are_refused_and_nothing_is_saved(self):
        from tenants.models import SiteSetting
        for bad in (dict(whatsapp='12345'), dict(contact_email='not-an-email'), dict(price_monthly='5'),
                    dict(price_yearly='abc'), dict(announcement='x' * 201)):
            resp = self.save(**bad)
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(SiteSetting.objects.exists(), bad)
            self.assertTrue(any(m.tags == 'error' for m in resp.context['messages']), bad)

    def test_blank_prices_go_back_to_the_standard_ones(self):
        self.save()
        self.save(price_monthly='', price_yearly='', announcement='')
        self.client.logout()
        page = self.client.get('/').content.decode()
        self.assertIn('Rs 700', page)
        self.assertNotIn('class="ann"', page)

    def test_a_notice_with_html_is_shown_as_plain_text(self):
        self.save(announcement='<script>alert(1)</script>')
        self.client.logout()
        page = self.client.get('/').content.decode()
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_only_a_platform_admin_can_open_the_settings(self):
        from django.contrib.auth import get_user_model
        self.client.logout()
        self.assertEqual(self.client.get('/superadmin/site/').status_code, 302)
        staff = get_user_model().objects.create_user('clerk-site', 'c@example.com', 'pw-12345-xx', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/superadmin/site/').status_code, 404)
        self.assertEqual(self.client.post('/superadmin/site/', self.FORM).status_code, 404)

    def test_the_busy_home_page_still_follows_the_seo_rules(self):
        import html as htmllib
        import re
        self.save()
        self.client.logout()
        page = self.client.get('/').content.decode()
        body = re.sub(r'<(script|style)[\s\S]*?</\1>', '', page)
        bold = re.findall(r'<(?:b|strong)>(.*?)</(?:b|strong)>', body, re.S)
        self.assertLessEqual(len(bold), 33)
        self.assertEqual(len(bold), len(set(bold)))
        headings = len(re.findall(r'<h[1-6][ >]', body))
        words = len(re.sub(r'<[^>]+>', ' ', body).split())
        self.assertGreaterEqual(words / headings, 80, (headings, words))
        texts = [htmllib.unescape(re.sub(r'<[^>]+>', ' ', i)).strip() for _, i in re.findall(r'<a\b([^>]*)>(.*?)</a>', body, re.S)]
        self.assertTrue(all(texts))
        self.assertEqual(len(texts), len(set(texts)), [t for t in texts if texts.count(t) > 1])
