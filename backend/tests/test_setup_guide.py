"""The Setup guide page, its menu entry, the welcome note and the data check. Owner only; it only reads data."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend import onboarding
from backend.models import Batch, Bill, Customer, Product, ShopProfile, Supplier, TwoFactor, Unit

from .helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class GuidePageTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.owner)

    def test_the_page_shows_every_step_with_why_tips_and_buttons(self):
        resp = self.client.get('/setup/')
        self.assertEqual(resp.status_code, 200)
        for title in ('Add your shop details', 'Add your suppliers', 'Add your medicines', 'Enter your stock', 'Make your first bill'):
            self.assertContains(resp, title)
        self.assertContains(resp, 'Never skip the expiry date')
        for url in ('/shop/', '/import/suppliers/?template=1', '/import/products/', '/import/stock/?template=1', '/purchases/new/', '/bills/new/'):
            self.assertContains(resp, f'href="{url}"')
        self.assertContains(resp, 'Also worth doing')
        self.assertContains(resp, 'Check your data')

    def test_every_button_goes_to_a_page_that_opens(self):
        for step in onboarding.steps():
            for _label, url in step['buttons']:
                self.assertEqual(self.client.get(url).status_code, 200, url)
        for e in onboarding.extras(self.owner):
            self.assertEqual(self.client.get(e['url']).status_code, 200, e['url'])
        for c in onboarding.data_checks():
            self.assertEqual(self.client.get(c['url']).status_code, 200, c['url'])

    def test_progress_follows_the_data(self):
        g = onboarding.guide(self.owner)
        self.assertEqual((g['done'], g['total']), (2, 5))               # StockMixin made a supplier and a product
        self.assertContains(self.client.get('/setup/'), '2 of 5 steps done')
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Shop', address='1 Main Road', phone='9876543210')
        self.buy()
        bill = self.new_bill()
        Bill.objects.filter(pk=bill.pk).update(status='FINAL')
        page = self.client.get('/setup/')
        self.assertContains(page, '5 of 5 steps done')
        self.assertContains(page, 'All the basic steps are done')

    def test_the_extras_tick_themselves(self):
        def done():
            return {e['title']: e['done'] for e in onboarding.extras(self.owner)}
        before = done()
        self.assertFalse(before['Customers who buy on credit'])
        self.assertFalse(before['Turn on 2-step sign-in'])
        self.assertIsNone(before['Download a backup copy'])
        Customer.objects.create(name='Asha')
        TwoFactor.objects.create(user=self.owner)
        Supplier.objects.filter(pk=self.supplier.pk).update(opening_balance=Decimal('500'))
        get_user_model().objects.create_user('clerk', password='pw-12345-xx')
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Shop', gstin='22AAAAA0000A1Z5', drug_license='DL-1')
        after = done()
        for title in ('Customers who buy on credit', 'Turn on 2-step sign-in', 'What you still owe your suppliers',
                      'Logins for your staff', 'GSTIN and Drug Licence number'):
            self.assertTrue(after[title], title)

    def test_the_data_check_counts_real_mistakes(self):
        def counts():
            return {c['title']: c['count'] for c in onboarding.data_checks()}
        start = counts()
        self.assertEqual(start['Suppliers without a phone number'], 1)
        self.assertEqual(start['Medicines without a unit'], 0)
        Product.objects.create(name='No unit medicine', unit=None)
        self.buy()
        Batch.objects.update(expire_date=None)
        Supplier.objects.create(company='No phone Co', phone='')
        after = counts()
        self.assertEqual(after['Medicines without a unit'], 1)
        self.assertEqual(after['Batches with stock but no expiry date'], 1)
        self.assertEqual(after['Suppliers without a phone number'], 2)
        self.assertGreaterEqual(after['Medicines without an MRP'], 1)
        page = self.client.get('/setup/')
        self.assertContains(page, 'Medicines without a unit: 1')
        self.assertContains(page, 'Batches with stock but no expiry date: 1')

    def test_a_clean_shop_says_all_good(self):
        Supplier.objects.update(phone='9876543210')
        Product.objects.update(mrp=Decimal('10'))
        page = self.client.get('/setup/')
        self.assertNotContains(page, 'Medicines without a unit:')
        self.assertContains(page, 'All good.')

    def test_one_failing_part_never_breaks_the_page(self):
        with mock.patch('backend.onboarding.steps', side_effect=RuntimeError('boom')):
            resp = self.client.get('/setup/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'could not check your progress')
        self.assertContains(resp, 'Also worth doing')
        with mock.patch('backend.onboarding.Customer.objects') as objs:
            objs.exists.side_effect = RuntimeError('boom')
            self.assertEqual(self.client.get('/setup/').status_code, 200)
        with mock.patch('backend.models.Batch.objects') as objs:
            objs.filter.side_effect = RuntimeError('boom')
            page = self.client.get('/setup/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Could not be checked just now.')

    def test_only_the_owner_gets_in_and_the_menu_has_the_entry(self):
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345-xx')
        self.client.force_login(clerk)
        self.assertEqual(self.client.get('/setup/').status_code, 403)
        self.assertNotContains(self.client.get('/help/'), 'href="/setup/"')
        self.client.logout()
        resp = self.client.get('/setup/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/help/'), 'href="/setup/"')


@override_settings(**SAFE)
class WelcomeTests(StockMixin, TestCase):
    """A brand new shop (nothing done yet) gets the welcome note; a shop that already has data does not."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx'))

    def test_a_shop_with_data_gets_the_card_but_no_welcome_note(self):
        page = self.client.get('/')
        self.assertContains(page, 'Get your shop ready')
        self.assertContains(page, 'Open the setup guide')
        self.assertNotContains(page, 'id="welcome"')

    def test_a_brand_new_shop_gets_the_welcome_note(self):
        Product.objects.all().delete()
        Supplier.objects.all().delete()
        Unit.objects.all().delete()
        ShopProfile.objects.all().delete()
        self.assertTrue(onboarding.checklist(get_user_model().objects.get(username='boss'))['fresh'])
        page = self.client.get('/')
        self.assertContains(page, 'id="welcome"')
        self.assertContains(page, 'Welcome to MedSync')
        self.assertContains(page, 'href="/setup/" id="welcome-go"')
        self.assertContains(page, 'ms-welcome-v1')                       # shown once per browser
        self.client.post('/help/hide-setup/')
        self.assertNotContains(self.client.get('/'), 'id="welcome"')

    def test_a_finished_shop_sees_neither(self):
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Shop', address='1 Main Road', phone='9876543210')
        self.buy()
        bill = self.new_bill()
        Bill.objects.filter(pk=bill.pk).update(status='FINAL')
        page = self.client.get('/')
        self.assertNotContains(page, 'Get your shop ready')
        self.assertNotContains(page, 'id="welcome"')
