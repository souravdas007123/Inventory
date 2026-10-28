"""The Customers and Billing lists work out everything for the whole page at once (a few queries, not several per row),
and the figures are exactly the same as when each customer is worked out alone."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection

from backend.models import Customer, CustomerPayment, SalesReturn, SalesReturnItem

from .helpers import StockMixin


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class ListSpeedTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy(qty=50)
        self.customers = [Customer.objects.create(name=f'Cust {i}', phone=f'98765432{i:02d}') for i in range(12)]
        for i, c in enumerate(self.customers):
            bill = self.new_bill(customer=c, payment_mode='CREDIT', amount_paid=Decimal('5.00'))
            self.sell(qty=3 + i, bill=bill)
        CustomerPayment.objects.create(customer=self.customers[1], amount=Decimal('10.00'))
        CustomerPayment.objects.create(customer=self.customers[1], amount=Decimal('7.00'))
        bill = self.customers[2].bills.first()
        ret = SalesReturn.objects.create(bill=bill, refund_mode='ADJUST')
        SalesReturnItem.objects.create(sales_return=ret, invoice_item=bill.items.first(), qty=1)
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)

    def queries(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url, {'per': 100}).status_code, 200)
        return len(ctx.captured_queries)

    def queries_after_more(self, url):
        """More customers and bills must not mean more queries (the number itself differs between set-ups)."""
        for i in range(12, 22):
            c = Customer.objects.create(name=f'Cust {i}', phone=f'98765432{i:02d}')
            self.sell(qty=2, bill=self.new_bill(customer=c, payment_mode='CREDIT', amount_paid=Decimal('1.00')))
        return self.queries(url)

    def test_bulk_balances_equal_the_single_balance(self):
        fresh = list(Customer.objects.order_by('pk'))
        Customer.bulk_balances(fresh)
        for c in fresh:
            self.assertEqual(c._balance, Customer.objects.get(pk=c.pk).balance(), c.name)
        self.assertTrue(any(c._balance > 0 for c in fresh))

    def test_bulk_balances_with_no_customers(self):
        Customer.bulk_balances([])

    def test_customers_page_shows_the_same_balances_with_few_queries(self):
        html = self.client.get('/customers/', {'per': 100}).content.decode()
        self.assertEqual(self.queries('/customers/'), self.queries_after_more('/customers/'))
        for c in self.customers:
            self.assertIn(c.name, html)
        csv = self.client.get('/customers/', {'export': 'csv'}).content.decode()
        self.assertIn('Cust 1,9876543201,\'-,No limit,%s,Active' % float(Customer.objects.get(pk=self.customers[1].pk).balance()), csv)

    def test_billing_page_has_a_fixed_number_of_queries(self):
        self.assertEqual(self.queries('/bills/'), self.queries_after_more('/bills/'))
