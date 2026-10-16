import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import Bill, Purchase
from backend.views import CONFIG

from .helpers import StockMixin


def embedded(html, element_id):
    """The JSON that json_script put into the page."""
    m = re.search(r'<script id="%s" type="application/json">(.*?)</script>' % element_id, html, re.S)
    assert m, f'{element_id} is not in the page'
    return json.loads(m.group(1))


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)  # the live site forces https; tests talk plain http
class OwnerPagesTests(StockMixin, TestCase):
    """Every page opens for the owner (nothing crashes), with some data in the shop."""

    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.purchase, self.pitem = self.buy(selling='100.00')
        self.bill, self.item = self.sell(qty=10)

    def test_every_list_page_opens(self):
        for key in CONFIG:
            with self.subTest(key=key):
                resp = self.client.get(reverse('list', kwargs={'key': key}))
                self.assertIn(resp.status_code, (200, 302), key)
                if resp.status_code == 200:
                    resp = self.client.get(reverse('list', kwargs={'key': key}), {'q': 'a', 'per': 25, 'page': 2})
                    self.assertEqual(resp.status_code, 200, key)

    def test_every_list_can_be_exported_as_csv(self):
        for key in ('bills', 'purchases', 'products', 'batches', 'suppliers'):
            with self.subTest(key=key):
                resp = self.client.get(reverse('list', kwargs={'key': key}), {'export': 'csv'})
                self.assertEqual(resp.status_code, 200, key)

    def test_every_form_page_opens(self):
        for key, cfg in CONFIG.items():
            if cfg.get('readonly') or cfg.get('no_new') or cfg.get('start_url'):
                continue
            with self.subTest(key=key):
                self.assertEqual(self.client.get(reverse('new', kwargs={'key': key})).status_code, 200, key)

    def test_edit_pages_open(self):
        self.assertEqual(self.client.get(reverse('edit', kwargs={'key': 'purchases', 'pk': self.purchase.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse('edit', kwargs={'key': 'products', 'pk': self.product.pk})).status_code, 200)
        self.assertEqual(self.client.get(reverse('edit', kwargs={'key': 'suppliers', 'pk': self.supplier.pk})).status_code, 200)

    def test_other_pages_open(self):
        urls = ['/', '/report/', '/shop/', '/gst/', '/drug_register/', '/reorder/', '/refill/', '/expiry/', '/salary/',
                '/supplier_prices/', f'/supplier_prices/{self.product.pk}/', '/backup/', '/import/', '/import/stock/',
                '/import/products/', reverse('invoice', args=[self.bill.pk]),
                reverse('sales_return_new', args=[self.bill.pk]), reverse('purchase_return_new', args=[self.purchase.pk])]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_billing_list_has_the_preview_data_of_every_bill(self):
        html = self.client.get('/bills/').content.decode()
        data = embedded(html, 'bill-data')
        d = data[str(self.bill.pk)]
        self.assertTrue(d['ok'])
        self.assertEqual(d['count'], 1)
        self.assertEqual(d['items'][0]['name'], 'Dolo 650')
        self.assertEqual(d['items'][0]['qty'], '10 Tablet')
        self.assertTrue(d['items'][0]['batch'].startswith('BATCH-'), d['items'][0]['batch'])
        self.assertEqual(d['totals']['grand'], '₹112.00')
        self.assertEqual(d['invoice_url'], reverse('invoice', args=[self.bill.pk]))

    def test_purchase_list_has_the_preview_data_of_every_purchase(self):
        html = self.client.get('/purchases/').content.decode()
        data = embedded(html, 'purchase-data')
        d = data[str(self.purchase.pk)]
        self.assertEqual(d['count'], 1)
        self.assertEqual(d['supplier'], 'ABC Pharma')
        self.assertEqual(d['items'][0]['qty'], '5 Strip')
        self.assertEqual(d['items'][0]['mrp'], '₹100.00')
        self.assertEqual(d['totals']['total'], '₹560.00')
        self.assertEqual(d['short'], 0)
        self.assertTrue(d['edit_url'] and d['return_url'])

    def test_short_expiry_is_flagged_in_the_purchase_preview(self):
        self.buy(days=30, invoice='SHORT')
        data = embedded(self.client.get('/purchases/').content.decode(), 'purchase-data')
        flagged = [d for d in data.values() if d['short']]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]['items'][0]['flag'], 'soon')

    def test_page_title_is_clean(self):
        for url in ('/purchases/', '/bills/', '/products/'):
            with self.subTest(url=url):
                title = re.search(r'<title>(.*?)</title>', self.client.get(url).content.decode(), re.S).group(1)
                self.assertNotIn('<', title)
                self.assertNotIn('{', title)
                self.assertLess(len(title), 60)

    def test_preview_api_matches_the_embedded_data(self):
        api = self.client.get(reverse('bill_preview', args=[self.bill.pk])).json()
        embedded_data = embedded(self.client.get('/bills/').content.decode(), 'bill-data')[str(self.bill.pk)]
        self.assertEqual(api, embedded_data)
        api = self.client.get(reverse('purchase_preview', args=[self.purchase.pk])).json()
        embedded_data = embedded(self.client.get('/purchases/').content.decode(), 'purchase-data')[str(self.purchase.pk)]
        self.assertEqual(api, embedded_data)

    def test_preview_of_a_missing_record_is_404(self):
        self.assertEqual(self.client.get(reverse('bill_preview', args=[99999])).status_code, 404)
        self.assertEqual(self.client.get(reverse('purchase_preview', args=[99999])).status_code, 404)

    def test_cancel_a_bill_from_the_page(self):
        resp = self.client.post(reverse('bill_cancel', args=[self.bill.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Bill.objects.get(pk=self.bill.pk).status, 'CANCELLED')
        self.assertEqual(self.stock(), 50)

    def test_cancel_needs_post(self):
        self.assertEqual(self.client.get(reverse('bill_cancel', args=[self.bill.pk])).status_code, 405)

    def test_a_purchase_can_be_deleted_from_the_page_when_unsold(self):
        second, _ = self.buy(invoice='SECOND')            # 50 more Tablets: stock 40 + 50 = 90
        resp = self.client.post(reverse('delete', kwargs={'key': 'purchases', 'pk': second.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Purchase.objects.filter(pk=second.pk).exists())
        self.assertEqual(self.stock(), 40)

    def test_a_bill_cannot_be_deleted(self):
        self.client.post(reverse('delete', kwargs={'key': 'bills', 'pk': self.bill.pk}))
        self.assertTrue(Bill.objects.filter(pk=self.bill.pk).exists())

    def test_product_lookup_and_find(self):
        resp = self.client.get(reverse('product_find'), {'q': 'Dolo'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Dolo 650', resp.content.decode())


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)  # the live site forces https; tests talk plain http
class AccessTests(StockMixin, TestCase):
    def test_anonymous_users_are_sent_to_the_login_page(self):
        for url in ('/', '/bills/', '/purchases/', '/import/', '/backup/', '/api/bill_preview/1/'):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn('/login/', resp['Location'])

    def test_a_user_without_rights_cannot_see_the_shop_data(self):
        user = get_user_model().objects.create_user('clerk', password='pw-12345')
        self.client.force_login(user)
        _, _ = self.buy()
        bill, _ = self.sell(qty=1)
        for url in ('/purchases/', '/import/', '/import/stock/', '/backup/', '/gst/', '/report/',
                    reverse('bill_preview', args=[bill.pk]), reverse('invoice', args=[bill.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_backup_files_cannot_be_reached_by_path_tricks(self):
        owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(owner)
        for name in ('..%2F..%2Fdb.sqlite3', 'db.sqlite3', 'nothing.zip'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f'/backup/download/{name}/').status_code, 404)

    def test_login_works_and_wrong_passwords_are_locked_after_five_tries(self):
        get_user_model().objects.create_user('shop', password='right-pass-1')
        from django.core.cache import cache
        cache.clear()
        for _ in range(5):
            self.client.post('/login/', {'username': 'shop', 'password': 'wrong'})
        resp = self.client.post('/login/', {'username': 'shop', 'password': 'right-pass-1'})
        self.assertEqual(resp.status_code, 429)
        cache.clear()
        resp = self.client.post('/login/', {'username': 'shop', 'password': 'right-pass-1'})
        self.assertEqual(resp.status_code, 302)
