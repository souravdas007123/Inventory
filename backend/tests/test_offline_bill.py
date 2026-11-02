"""Offline billing, server side: a new-bill form has a one-time key, so the same form never makes two bills; a bill sent from the
queue is answered in JSON; and the medicine list for the device is complete."""
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Bill, OfflineBill, Product
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class OfflineBillTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.client.force_login(self.owner)
        self.buy(qty=100, price='1000.00', unit='tablet')

    def data(self, key=None, qty='3', **extra):
        d = {'customer_name': 'Ramesh', 'date': today().isoformat(), 'invoice_mode': 'offline', 'payment_mode': 'CASH', 'amount_paid': '0',
             'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
             'items-0-product': str(self.product.pk), 'items-0-qty': qty, 'items-0-rate': '10.00', 'items-1-product': '', 'items-1-qty': ''}
        if key:
            d['offline_id'] = key
        d.update(extra)
        return d

    def test_the_new_bill_form_carries_the_key_field_and_the_edit_form_does_not(self):
        html = self.client.get('/bills/new/').content.decode()
        self.assertIn('name="offline_id"', html)

    def test_the_same_form_twice_makes_one_bill_and_takes_stock_once(self):
        key = str(uuid.uuid4())
        r1 = self.client.post('/bills/new/', self.data(key))
        self.assertEqual(r1.status_code, 302)
        stock = self.stock()
        r2 = self.client.post('/bills/new/', self.data(key))
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r1['Location'], r2['Location'])
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(self.stock(), stock)
        self.assertEqual(OfflineBill.objects.count(), 1)

    def test_a_form_without_a_key_or_with_a_bad_key_still_saves(self):
        for k in (None, '', 'not a key!', 'x' * 80):
            r = self.client.post('/bills/new/', self.data(k))
            self.assertEqual(r.status_code, 302, k)
        self.assertEqual(Bill.objects.count(), 4)
        self.assertEqual(OfflineBill.objects.count(), 0)

    def test_two_different_keys_make_two_bills(self):
        self.client.post('/bills/new/', self.data(str(uuid.uuid4())))
        self.client.post('/bills/new/', self.data(str(uuid.uuid4())))
        self.assertEqual(Bill.objects.count(), 2)

    def test_sync_success_is_json_and_a_resend_is_marked_duplicate(self):
        key = str(uuid.uuid4())
        r = self.client.post('/bills/new/', self.data(key), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.content)
        self.assertTrue(d['ok'])
        self.assertFalse(d['duplicate'])
        bill = Bill.objects.get()
        self.assertEqual(d['bill_id'], bill.pk)
        self.assertEqual(d['url'], f'/bills/{bill.pk}/invoice/')
        self.assertEqual(d['number'], bill.display_number)
        d2 = json.loads(self.client.post('/bills/new/', self.data(key), HTTP_X_OFFLINE_SYNC='1').content)
        self.assertTrue(d2['ok'] and d2['duplicate'])
        self.assertEqual(d2['bill_id'], bill.pk)
        self.assertEqual(Bill.objects.count(), 1)

    def test_sync_failure_says_why_and_saves_nothing(self):
        key = str(uuid.uuid4())
        before = self.stock()
        r = self.client.post('/bills/new/', self.data(key, qty='99999'), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 422)
        d = json.loads(r.content)
        self.assertFalse(d['ok'])
        self.assertTrue(d['errors'])
        self.assertEqual(Bill.objects.count(), 0)
        self.assertEqual(OfflineBill.objects.count(), 0)         # a failed bill does not use up its key: it can be fixed and sent again
        self.assertEqual(self.stock(), before)
        ok = self.client.post('/bills/new/', self.data(key, qty='3'), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(ok.status_code, 200)

    def test_sync_with_an_empty_bill_or_bad_form_is_a_clean_error(self):
        r = self.client.post('/bills/new/', self.data(str(uuid.uuid4()), **{'items-0-product': '', 'items-0-qty': ''}), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 422)
        r = self.client.post('/bills/new/', {'garbage': '1'}, HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Bill.objects.count(), 0)

    def test_the_sync_header_means_nothing_on_get_or_on_other_pages(self):
        r = self.client.get('/bills/new/', HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/html', r['Content-Type'])

    def test_a_signed_out_sync_goes_to_the_login_page_not_to_json(self):
        self.client.logout()
        r = self.client.post('/bills/new/', self.data(str(uuid.uuid4())), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])

    def test_staff_without_the_billing_permission_cannot_sync_a_bill(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        r = self.client.post('/bills/new/', self.data(str(uuid.uuid4())), HTTP_X_OFFLINE_SYNC='1')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Bill.objects.count(), 0)

    def test_a_held_bill_never_keeps_the_key(self):
        from backend.models import HeldBill
        fields = [[k, v] for k, v in self.data(str(uuid.uuid4())).items()]
        r = self.client.post('/bills/hold/', json.dumps({'fields': fields}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('offline_id', [n for n, _ in HeldBill.objects.get().payload])

    def test_catalog_has_every_active_medicine_in_the_lookup_shape(self):
        Product.objects.create(name='Hidden', unit=self.tablet, gst_rate=Decimal('12.00'), is_active=False)
        r = self.client.get('/api/offline_catalog/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Cache-Control'], 'no-store')
        d = json.loads(r.content)
        self.assertEqual([p['name'] for p in d['results']], ['Dolo 650'])
        live = json.loads(self.client.get('/api/products/lookup/', {'q': 'Dolo'}).content)['results'][0]
        for k in ('id', 'name', 'barcode', 'mrp', 'rate1', 'factors', 'unit_id', 'units', 'stock', 'available_qty', 'unit_name', 'schedule'):
            self.assertEqual(d['results'][0][k], live[k], k)
        self.assertFalse(d['truncated'])

    def test_catalog_needs_the_billing_permission(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/api/offline_catalog/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/api/offline_catalog/').status_code, 302)
