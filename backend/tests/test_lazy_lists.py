"""Very long drop-downs (thousands of medicines or customers) are not written into the page: the page carries only the chosen
option and the list is fetched once. Short lists (a small shop) stay exactly as they were."""
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend import fastwidgets
from django.test import Client
from backend.models import Bill, Product, Purchase, PurchaseItem
from backend.models_extra import Customer
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class LazyListTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))
        self.others = [Product.objects.create(name=f'Medicine {i}', unit=self.tablet, gst_rate=Decimal('12')) for i in range(8)]
        self.customers = [Customer.objects.create(name=f'Cust {i}', phone=f'90000000{i:02d}') for i in range(8)]

    def lazy(self, minimum=3):
        return mock.patch.object(fastwidgets, 'LAZY_MIN', minimum)

    def test_a_short_list_is_left_exactly_as_it_was(self):
        html = self.client.get('/purchases/new/').content.decode()
        self.assertNotIn('data-lazy', html)
        self.assertIn('Medicine 7', html)
        self.assertNotIn('Object.defineProperty(proto', html)

    def test_a_long_list_is_not_written_into_the_page(self):
        with self.lazy():
            html = self.client.get('/purchases/new/').content.decode()
            bill = self.client.get('/bills/new/').content.decode()
        self.assertIn('data-lazy="/api/choices/product/"', html)
        self.assertNotIn('Medicine 7', html)
        self.assertIn('Object.defineProperty(proto', html)
        self.assertIn('data-lazy="/api/choices/customer/"', bill)
        self.assertNotIn('Cust 7', bill)
        self.assertNotIn('Medicine 7', bill)

    def test_the_page_stays_small_however_many_medicines_there_are(self):
        small = len(self.client.get('/purchases/new/').content)
        for i in range(300):
            Product.objects.create(name=f'Extra medicine number {i}', unit=self.tablet, gst_rate=Decimal('12'))
        big = len(self.client.get('/purchases/new/').content)                  # real LAZY_MIN: 150 is passed
        self.assertLess(big, small + 4000)
        self.assertNotIn('Extra medicine number 299', self.client.get('/purchases/new/').content.decode())

    def test_a_saved_row_shows_its_own_medicine(self):
        purchase, item = self.buy(qty=4, invoice='L-1')
        with self.lazy():
            html = self.client.get('/purchases/%d/' % purchase.pk).content.decode()
        self.assertIn('<option value="%d" selected>%s</option>' % (self.product.pk, self.product.name), html)
        self.assertNotIn('Medicine 7', html)

    def test_the_list_endpoint_gives_the_same_rows_and_labels(self):
        r = self.client.get('/api/choices/product/')
        self.assertEqual(r.status_code, 200)
        rows = json.loads(r.content)['options']
        self.assertEqual(rows, [[str(p.pk), str(p)] for p in Product.objects.all()])
        rows = json.loads(self.client.get('/api/choices/customer/').content)['options']
        self.assertEqual(rows, [[str(c.pk), str(c)] for c in Customer.objects.filter(is_active=True).order_by('name')])
        self.assertIn('Cust 3 (9000000003)', [x[1] for x in rows])

    def test_inactive_customers_are_not_offered(self):
        Customer.objects.filter(name='Cust 2').update(is_active=False)
        rows = json.loads(self.client.get('/api/choices/customer/').content)['options']
        self.assertNotIn('Cust 2 (9000000002)', [x[1] for x in rows])

    def test_unknown_list_and_missing_permission(self):
        self.assertEqual(self.client.get('/api/choices/secrets/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get('/api/choices/product/').status_code, 302)           # to the sign-in page
        self.client.force_login(get_user_model().objects.create_user('nobody', password='pw'))
        self.assertEqual(self.client.get('/api/choices/product/').status_code, 403)
        self.assertEqual(self.client.get('/api/choices/customer/').status_code, 403)

    def test_a_bill_and_a_purchase_still_save_with_the_lazy_lists(self):
        self.buy(qty=100, price='1000.00', unit='tablet')
        bill = {'customer_name': 'Ramesh', 'customer': str(self.customers[5].pk), 'date': today().isoformat(), 'invoice_mode': 'offline',
                'payment_mode': 'CASH', 'amount_paid': '0',
                'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
                'items-0-product': str(self.product.pk), 'items-0-qty': '3', 'items-0-rate': '10.00', 'items-1-product': '', 'items-1-qty': ''}
        with self.lazy():
            r = self.client.post('/bills/new/', bill)
        self.assertEqual(r.status_code, 302, r.content[:300])
        made = Bill.objects.get()
        self.assertEqual(made.customer_id, self.customers[5].pk)
        purchase = {'supplier': self.supplier.pk, 'invoice_number': 'LAZY-1', 'invoice_date': today().isoformat(),
                    'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
                    'items-0-product': str(self.others[3].pk), 'items-0-qty': '5', 'items-0-unit': str(self.tablet.pk),
                    'items-0-purchase_price': '250.00', 'items-0-expire_date': (today().replace(year=today().year + 2)).isoformat()}
        before = Purchase.objects.count()
        with self.lazy():
            r = self.client.post('/purchases/new/', purchase)
        self.assertEqual(r.status_code, 302, r.content[:400])
        self.assertEqual(Purchase.objects.count(), before + 1)
        self.assertEqual(Purchase.objects.get(invoice_number='LAZY-1').items.get().product_id, self.others[3].pk)

    def test_a_wrong_medicine_id_is_still_refused_and_the_page_redraws(self):
        data = {'supplier': self.supplier.pk, 'invoice_number': 'LAZY-2', 'invoice_date': today().isoformat(),
                'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
                'items-0-product': '99999', 'items-0-qty': '5', 'items-0-unit': str(self.tablet.pk), 'items-0-purchase_price': '250.00',
                'items-0-expire_date': today().replace(year=today().year + 2).isoformat()}
        with self.lazy():
            r = self.client.post('/purchases/new/', data)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Purchase.objects.filter(invoice_number='LAZY-2').count(), 0)
        data['items-0-product'] = 'abc'
        with self.lazy():
            self.assertEqual(self.client.post('/purchases/new/', data).status_code, 200)       # a junk id never breaks the page


from django.db import connection                                    # noqa: E402
from django.test.utils import CaptureQueriesContext                 # noqa: E402


@override_settings(**SAFE)
class SharedChoicesTests(StockMixin, TestCase):
    """Rows of a formset share one read of each drop-down list (it was one identical query per row)."""

    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_edit_page_query_count_does_not_grow_with_rows(self):
        purchase, item = self.buy(qty=4, invoice='S-1')
        def count():
            with CaptureQueriesContext(connection) as q:
                self.c.get('/purchases/%d/' % purchase.pk)
            return len(q)
        few = count()
        for _ in range(8):
            PurchaseItem.objects.create(purchase=purchase, product=self.product, qty=2, unit=self.strip,
                                        purchase_price='100.00', expire_date=item.expire_date)
        self.assertLessEqual(count(), few + 4)                        # a few more for the rows themselves, not 2 per row

    def test_the_saved_choice_is_still_selected(self):
        purchase, item = self.buy(qty=4, invoice='S-2')
        page = self.c.get('/purchases/%d/' % purchase.pk).content.decode()
        self.assertRegex(page, r'<option value="%d" selected>' % self.strip.pk)
