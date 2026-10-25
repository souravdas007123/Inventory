"""WhatsApp bill sharing: the message has the items, GST, total and what is due; one tap from the bill list and the invoice."""
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Customer, Product, ShopProfile
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


def decode(location):
    u = urlparse(location)
    return u.path, parse_qs(u.query).get('text', [''])[0]


@override_settings(**SAFE)
class BillWhatsAppTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.owner)
        ShopProfile.objects.all().delete()
        ShopProfile.objects.create(name='Sharma Medical')
        self.buy(qty=50)

    def test_the_message_has_shop_items_gst_total_and_goes_to_the_customer(self):
        cust = Customer.objects.create(name='Ramesh', phone='9876543210')
        bill, _ = self.sell(qty=10, rate='10.00', bill=self.new_bill(customer=cust, customer_name='Ramesh'))
        resp = self.client.get(f'/bills/{bill.pk}/whatsapp/')
        self.assertEqual(resp.status_code, 302)
        path, text = decode(resp['Location'])
        self.assertEqual(path, '/919876543210')
        for part in ('*Sharma Medical*', bill.display_number, 'Hello Ramesh,', 'Dolo 650', 'GST included', f'*Total: ₹{bill.grand_total}*',
                     'Paid by', 'Thank you'):
            self.assertIn(part, text)

    def test_a_cash_bill_has_no_greeting_and_no_number(self):
        bill, _ = self.sell(qty=1)
        resp = self.client.get(f'/bills/{bill.pk}/whatsapp/')
        path, text = decode(resp['Location'])
        self.assertEqual(path, '/')                     # WhatsApp lets the user pick a contact
        self.assertNotIn('Hello', text)

    def test_a_credit_bill_shows_what_is_due(self):
        cust = Customer.objects.create(name='Sita', phone='9123456789')
        bill = self.new_bill(customer=cust, customer_name='Sita', payment_mode='CREDIT', amount_paid=Decimal('20.00'))
        self.sell(qty=10, rate='10.00', bill=bill)
        bill.refresh_from_db()
        _, text = decode(self.client.get(f'/bills/{bill.pk}/whatsapp/')['Location'])
        self.assertIn('Paid now: ₹20.00', text)
        self.assertIn(f'due: ₹{bill.credit_due:.2f}', text)

    def test_a_long_bill_is_shortened_but_the_total_stays(self):
        bill = self.new_bill()
        for i in range(15):
            p = Product.objects.create(name=f'Medicine {i}', unit=self.tablet, gst_rate=Decimal('12.00'))
            self.buy(qty=5, unit='tablet', product=p, invoice=f'INV-X{i}')
            self.sell(qty=1, rate='10.00', bill=bill, product=p)
        _, text = decode(self.client.get(f'/bills/{bill.pk}/whatsapp/')['Location'])
        self.assertIn('+ 3 more items', text)
        self.assertNotIn('Medicine 14', text)
        self.assertIn('*Total:', text)
        self.assertLessEqual(len(text), 1500)

    def test_a_cancelled_bill_cannot_be_shared(self):
        bill, _ = self.sell(qty=1)
        bill.cancel()
        resp = self.client.get(f'/bills/{bill.pk}/whatsapp/', follow=True)
        self.assertContains(resp, 'cancelled bill cannot be shared')

    def test_the_bill_list_and_the_invoice_have_the_button(self):
        bill, _ = self.sell(qty=1)
        self.assertContains(self.client.get('/bills/'), f'/bills/{bill.pk}/whatsapp/')
        invoice = self.client.get(f'/bills/{bill.pk}/invoice/')
        self.assertContains(invoice, 'wa.me/')
        self.assertContains(invoice, 'Dolo%20650')
        bill.cancel()
        self.assertNotContains(self.client.get(f'/bills/{bill.pk}/invoice/'), '💬 WhatsApp')
        self.assertNotContains(self.client.get('/bills/'), f'/bills/{bill.pk}/whatsapp/')

    def test_a_user_without_billing_rights_cannot_use_it(self):
        bill, _ = self.sell(qty=1)
        self.client.force_login(get_user_model().objects.create_user('nobody', password='pw-12345-xx'))
        resp = self.client.get(f'/bills/{bill.pk}/whatsapp/')
        self.assertNotIn('wa.me', resp.get('Location', ''))
