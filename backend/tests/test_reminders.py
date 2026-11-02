"""Payment reminders: who owes, since when, the WhatsApp message, and that nothing is sent by the server."""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend import reminders
from backend.models import Customer, CustomerPayment
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
T = today()


@override_settings(**SAFE)
class ReminderTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.client.force_login(self.owner)
        self.buy(qty=100, price='1000.00', unit='tablet')
        self.ram = Customer.objects.create(name='Ram Kumar', phone='9876543210')
        self.sita = Customer.objects.create(name='Sita Devi', phone='')
        self.paid_up = Customer.objects.create(name='Paid Up', phone='9876500000')

    def credit(self, customer, qty, days_ago, rate='10.00'):
        bill = self.new_bill(date=T - timedelta(days=days_ago), customer=customer, customer_name=customer.name, payment_mode='CREDIT', status='FINAL')
        self.sell(qty=qty, rate=rate, bill=bill)
        return bill

    def rows(self, **params):
        r = self.client.get('/reminders/', params)
        self.assertEqual(r.status_code, 200)
        return {x['customer'].name: x for x in r.context['rows']}

    def test_lists_only_customers_with_a_balance_biggest_first(self):
        self.credit(self.ram, 10, 40)                         # 100 + 12% GST = 112
        self.credit(self.sita, 20, 3)
        self.credit(self.paid_up, 5, 10)
        CustomerPayment.objects.create(customer=self.paid_up, amount=Decimal('56.00'), date=T)
        rows = self.rows()
        self.assertEqual(list(rows), ['Sita Devi', 'Ram Kumar'])
        self.assertEqual(rows['Ram Kumar']['balance'], Decimal('112.00'))
        self.assertEqual(rows['Ram Kumar']['days'], 40)
        self.assertFalse(rows['Sita Devi']['phone_ok'])

    def test_a_receipt_clears_the_oldest_bill_first(self):
        self.credit(self.ram, 10, 50)       # 112, oldest
        self.credit(self.ram, 10, 5)        # 112
        CustomerPayment.objects.create(customer=self.ram, amount=Decimal('112.00'), date=T)
        r = self.rows()['Ram Kumar']
        self.assertEqual(r['balance'], Decimal('112.00'))
        self.assertEqual(r['days'], 5)      # the old bill is paid, so the debt is 5 days old
        CustomerPayment.objects.create(customer=self.ram, amount=Decimal('50.00'), date=T)
        self.assertEqual(self.rows()['Ram Kumar']['days'], 5)    # still the second bill, partly paid

    def test_the_age_is_counted_from_the_oldest_unpaid_bill(self):
        self.credit(self.ram, 10, 40)
        self.credit(self.ram, 10, 3)
        self.assertEqual(self.rows()['Ram Kumar']['days'], 40)
        CustomerPayment.objects.create(customer=self.ram, amount=Decimal('50.00'), date=T)       # part of the old bill is paid: it is still the oldest debt
        self.assertEqual(self.rows()['Ram Kumar']['days'], 40)
        self.assertEqual(self.rows()['Ram Kumar']['balance'], Decimal('174.00'))

    def test_filters(self):
        self.credit(self.ram, 10, 40)
        self.credit(self.sita, 10, 3)
        self.assertEqual(list(self.rows(days='30')), ['Ram Kumar'])
        self.assertEqual(len(self.rows(days='nonsense')), 2)
        Customer.objects.filter(pk=self.ram.pk).update(last_reminded=T - timedelta(days=2))
        self.assertEqual(list(self.rows(skip='1')), ['Sita Devi'])
        Customer.objects.filter(pk=self.ram.pk).update(last_reminded=T - timedelta(days=9))
        self.assertEqual(len(self.rows(skip='1')), 2)

    def test_whatsapp_button_notes_the_day_and_redirects_to_a_ready_message(self):
        self.credit(self.ram, 10, 40)
        r = self.client.post(f'/reminders/{self.ram.pk}/send/')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith('https://wa.me/919876543210?text='))
        self.assertIn('112.00', unquote(r['Location']))
        self.assertIn('Ram Kumar', unquote(r['Location']))
        self.ram.refresh_from_db()
        self.assertEqual(self.ram.last_reminded, T)

    def test_no_reminder_without_a_phone_or_a_balance_and_never_by_get(self):
        self.credit(self.sita, 10, 4)
        r = self.client.post(f'/reminders/{self.sita.pk}/send/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], '/reminders/')
        self.sita.refresh_from_db()
        self.assertIsNone(self.sita.last_reminded)
        r = self.client.post(f'/reminders/{self.paid_up.pk}/send/')
        self.assertEqual(r['Location'], '/reminders/')
        self.assertEqual(self.client.get(f'/reminders/{self.ram.pk}/send/').status_code, 405)
        self.assertEqual(self.client.post('/reminders/999999/send/').status_code, 404)

    def test_page_needs_a_login_and_the_customer_permission(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/reminders/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/reminders/').status_code, 302)

    def test_empty_page_says_so(self):
        self.assertIn('No customer owes you money', self.client.get('/reminders/').content.decode())

    def test_days_from(self):
        self.assertEqual(reminders.days_from('30'), 30)
        for bad in (None, '', 'x', '31', '-1', '9' * 30):
            self.assertEqual(reminders.days_from(bad), 0)
