"""Automatic purchase orders: a sale that makes a product low adds it to a PENDING order; the hourly sweep does the same for a
product that became low another way (write-off, return, stock count) and has sold lately."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from backend import levels
from backend.models import Product, ProductUnit, PurchaseOrder, PurchaseOrderItem, Supplier, Unit

from .helpers import StockMixin, today


def items():
    return [(i.product.name, i.order_qty, i.purchase_order.supplier.company, i.purchase_order.status) for i in PurchaseOrderItem.objects.select_related('product', 'purchase_order__supplier')]


class AutoOrderTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def make_low(self, product=None, left=2):
        """Stock is brought down WITHOUT a sale (like a write-off or a stock count would do)."""
        Product.objects.filter(pk=(product or self.product).pk).update(stock_qty=left)

    def test_a_sale_that_makes_the_product_low_creates_a_pending_order(self):
        self.buy(qty=5)                                   # 50 tablets
        self.sell(qty=48)                                 # 2 left
        self.assertEqual(items(), [('Dolo 650', 50, 'ABC Pharma', 'PENDING')])

    def test_no_order_while_the_stock_is_enough(self):
        self.buy(qty=5)
        self.sell(qty=5)
        self.assertEqual(items(), [])

    def test_no_order_when_the_product_is_already_on_a_pending_or_sent_order(self):
        self.buy(qty=5)
        self.sell(qty=48)
        self.sell(qty=1)                                  # sold again while the order waits
        self.assertEqual(PurchaseOrderItem.objects.count(), 1)
        PurchaseOrder.objects.update(status='SENT')
        self.sell(qty=1)
        self.assertEqual(PurchaseOrderItem.objects.count(), 1)

    def test_no_order_when_no_supplier_is_known(self):
        self.buy(qty=5)
        PurchaseOrder.objects.all().delete()
        from backend.models import PurchaseItem
        PurchaseItem.objects.filter(purchase_price__isnull=False).update(purchase_price=None)       # no price history
        Product.objects.filter(pk=self.product.pk).update(default_supplier=None)
        self.sell(qty=48)
        self.assertEqual(items(), [])

    def test_sweep_adds_a_product_that_became_low_without_a_sale(self):
        self.buy(qty=5)
        self.sell(qty=10)                                 # it sells, so it has recent sales
        self.make_low()                                   # then a write-off / stock count leaves 2
        self.assertEqual(items(), [])                     # nothing yet: no sale happened after
        self.assertEqual(levels.sweep(), 1)
        self.assertEqual(len(items()), 1)
        self.assertEqual(levels.sweep(), 0)               # asking again adds nothing (already on an order)
        self.assertEqual(PurchaseOrderItem.objects.count(), 1)

    def test_sweep_ignores_products_with_no_recent_sales(self):
        self.buy(qty=1)
        self.make_low()
        self.assertEqual(levels.sweep(), 0)
        self.assertEqual(items(), [])

    def test_sweep_ignores_sales_older_than_the_window(self):
        self.buy(qty=5)
        bill, _ = self.sell(qty=10)
        bill.date = today() - timedelta(days=45)
        bill.save()
        self.make_low()
        self.assertEqual(levels.sweep(), 0)

    def test_sweep_ignores_inactive_products(self):
        self.buy(qty=5)
        self.sell(qty=10)
        Product.objects.filter(pk=self.product.pk).update(is_active=False, stock_qty=2)
        self.assertEqual(levels.sweep(), 0)

    def test_sweep_puts_products_of_one_supplier_on_one_order(self):
        other = Product.objects.create(name='Crocin', unit=self.tablet, gst_rate=Decimal('12.00'))
        ProductUnit.objects.create(product=other, unit=self.strip, factor=10)
        self.buy(qty=5, invoice='A')
        self.buy(qty=5, invoice='B', product=other)
        self.sell(qty=10)
        self.sell(qty=10, product=other)
        self.make_low()
        self.make_low(other)
        self.assertEqual(levels.sweep(), 2)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.get().items.count(), 2)

    def test_one_failing_product_does_not_stop_the_others(self):
        other = Product.objects.create(name='Crocin', unit=self.tablet, gst_rate=Decimal('12.00'))
        ProductUnit.objects.create(product=other, unit=self.strip, factor=10)
        self.buy(qty=5, invoice='A')
        self.buy(qty=5, invoice='B', product=other)
        self.sell(qty=10)
        self.sell(qty=10, product=other)
        self.make_low()
        self.make_low(other)
        real = levels.maybe_create_order
        calls = []

        def flaky(product, today=None):
            calls.append(product.pk)
            if len(calls) == 1:
                raise RuntimeError('boom')
            return real(product, today)

        with mock.patch.object(levels, 'maybe_create_order', side_effect=flaky):
            self.assertEqual(levels.sweep(), 1)
        self.assertEqual(len(calls), 2)

    def test_the_sweep_runs_at_most_once_an_hour(self):
        self.buy(qty=5)
        self.sell(qty=10)
        self.make_low()
        self.assertEqual(levels.sweep_hourly(), 1)
        PurchaseOrderItem.objects.all().delete()
        self.assertEqual(levels.sweep_hourly(), 0)        # within the hour: not asked again
        cache.clear()
        self.assertEqual(levels.sweep_hourly(), 1)

    def test_visiting_a_page_runs_the_sweep(self):
        from django.contrib.auth import get_user_model
        self.buy(qty=5)
        self.sell(qty=10)
        self.make_low()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345'))
        with self.settings(SECURE_SSL_REDIRECT=False):
            self.assertEqual(self.client.get('/products/').status_code, 200)
        self.assertEqual(len(items()), 1)

    def test_a_failing_sweep_never_breaks_a_page_or_the_daily_task(self):
        with mock.patch.object(levels, 'sweep', side_effect=RuntimeError('boom')):
            self.assertEqual(levels.sweep_hourly(), 0)
            levels.refresh_daily()                         # must not raise
