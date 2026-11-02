"""A customer's buying history: the billing helper (JSON) and the History page."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Customer, Product
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
T = today()


@override_settings(**SAFE)
class HistoryTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.client.force_login(self.owner)
        self.other = Product.objects.create(name='Pantocid 40', unit=self.tablet, gst_rate=Decimal('12.00'))
        self.buy(qty=500, price='1000.00', unit='tablet')
        self.buy(qty=500, price='1000.00', unit='tablet', product=self.other, invoice='INV-2')
        self.ram = Customer.objects.create(name='Ram Kumar', phone='9876543210')
        self.sita = Customer.objects.create(name='Sita', phone='9000000001')

    def bill(self, customer, days_ago, items, **kw):
        b = self.new_bill(date=T - timedelta(days=days_ago), customer=customer, customer_name=customer.name, status='FINAL', **kw)
        for product, qty in items:
            self.sell(qty=qty, product=product, bill=b)
        return b

    def api(self, **params):
        r = self.client.get('/api/customer_history/', params)
        self.assertEqual(r.status_code, 200)
        return json.loads(r.content)

    def test_last_bills_and_usual_medicines(self):
        for d in (60, 30, 10):
            self.bill(self.ram, d, [(self.product, 10)])
        self.bill(self.ram, 5, [(self.other, 5), (self.product, 2)])
        self.bill(self.sita, 3, [(self.other, 1)])
        d = self.api(id=self.ram.pk)
        self.assertTrue(d['found'])
        self.assertEqual(len(d['bills']), 4)
        self.assertEqual([i['name'] for i in d['bills'][0]['items']], ['Pantocid 40', 'Dolo 650'])     # newest first
        self.assertEqual(d['usual'][0]['name'], 'Dolo 650')
        self.assertEqual(d['usual'][0]['times'], 4)
        self.assertEqual([(u['name'], u['times']) for u in d['usual']], [('Dolo 650', 4), ('Pantocid 40', 1)])   # Sita's Pantocid is not counted
        self.assertNotIn('Sita', json.dumps(d))                                                     # another customer's bills never leak

    def test_only_final_bills_and_active_medicines_count(self):
        self.bill(self.ram, 5, [(self.product, 1)], )
        draft = self.bill(self.ram, 4, [(self.other, 1)])
        type(draft).objects.filter(pk=draft.pk).update(status='CANCELLED')
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        d = self.api(id=self.ram.pk)
        self.assertEqual(len(d['bills']), 1)
        self.assertEqual(d['usual'], [])

    def test_at_most_five_bills(self):
        for d in range(1, 9):
            self.bill(self.ram, d, [(self.product, 1)])
        self.assertEqual(len(self.api(id=self.ram.pk)['bills']), 5)

    def test_bad_ids_are_safe(self):
        for bad in ('', 'x', '0', '-1', '9' * 30, '99999999'):
            self.assertEqual(self.api(id=bad), {'found': False}, bad)

    def test_needs_a_login_and_billing_permission(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/api/customer_history/', {'id': self.ram.pk}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/api/customer_history/', {'id': self.ram.pk}).status_code, 302)

    def test_history_page_finds_by_customer_patient_and_doctor(self):
        self.bill(self.ram, 5, [(self.product, 2), (self.other, 1)], patient_name='Gita Kumar', doctor_name='Mehta')
        self.bill(self.sita, 3, [(self.other, 1)], doctor_name='Rao')
        def found(**p):
            r = self.client.get('/history/', p)
            self.assertEqual(r.status_code, 200)
            return [x['bill'].customer_name for x in r.context['rows']]
        self.assertEqual(found(), [])
        self.assertEqual(found(customer='ram'), ['Ram Kumar'])
        self.assertEqual(found(customer='9000000001'), ['Sita'])
        self.assertEqual(found(patient='gita'), ['Ram Kumar'])
        self.assertEqual(found(doctor='Mehta'), ['Ram Kumar'])
        self.assertEqual(found(doctor='Mehta', customer='sita'), [])
        self.assertEqual(found(doctor='nobody'), [])
        r = self.client.get('/history/', {'doctor': 'Mehta'})
        self.assertIn('Dolo 650', r.content.decode())

    def test_history_page_ignores_cancelled_bills_and_odd_input(self):
        b = self.bill(self.ram, 5, [(self.product, 1)])
        type(b).objects.filter(pk=b.pk).update(status='CANCELLED')
        self.assertEqual(self.client.get('/history/', {'customer': 'ram'}).context['rows'], [])
        for odd in ("' OR 1=1 --", '<script>', 'x' * 500, '%', '\x00'):
            self.assertEqual(self.client.get('/history/', {'customer': odd, 'doctor': odd}).status_code, 200)
