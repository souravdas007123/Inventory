"""The Dashboard and the daily summary work out every customer's balance at once, so more credit customers do not mean more
queries, and the figures are the same as working each customer out alone."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from backend import reports, summary
from backend.models import Customer, CustomerPayment

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class DashboardSpeedTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy(qty=500, price='1000.00')
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.client.force_login(self.owner)
        self.add_customers(0, 6)
        reports.dashboard_overview(include_money=True)   # the first call creates the shop profile row

    def add_customers(self, a, b):
        for i in range(a, b):
            c = Customer.objects.create(name=f'Cust {i}', phone=f'98765432{i:02d}')
            self.sell(qty=2 + i, bill=self.new_bill(customer=c, customer_name=c.name, payment_mode='CREDIT', status='FINAL'))

    def count(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx.captured_queries)

    def test_overview_queries_do_not_grow_with_customers(self):
        before = self.count(lambda: reports.dashboard_overview(include_money=True))
        self.add_customers(6, 16)
        self.assertEqual(self.count(lambda: reports.dashboard_overview(include_money=True)), before)

    def test_daily_summary_queries_do_not_grow_with_customers(self):
        before = self.count(lambda: summary.daily_summary(today()))
        self.add_customers(6, 16)
        self.assertEqual(self.count(lambda: summary.daily_summary(today())), before)

    def test_figures_match_the_single_balance(self):
        c = Customer.objects.get(name='Cust 1')
        CustomerPayment.objects.create(customer=c, amount=Decimal('5.00'), date=today())
        out = reports.dashboard_overview(include_money=True)
        want = {x.name: x.balance() for x in Customer.objects.all() if x.balance() > 0}
        self.assertEqual({r['name']: r['balance'] for r in out['credit_rows']}, dict(sorted(want.items(), key=lambda kv: -kv[1])[:6]))
        self.assertEqual(out['credit_total'], sum(want.values()))
        self.assertEqual(summary.daily_summary(today())['outstanding'], sum(x.balance() for x in Customer.objects.filter(is_active=True)))

    def test_dashboard_page_renders(self):
        self.assertEqual(self.client.get('/').status_code, 200)
