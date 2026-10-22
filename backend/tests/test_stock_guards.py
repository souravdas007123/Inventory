"""Duplicate purchase invoices are refused, and a purchase cannot be edited below what was already sold."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from backend.models import Batch, Product, Purchase, PurchaseItem, Supplier

from .helpers import StockMixin, today


class DuplicateInvoiceTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy(invoice='INV-1')

    def new(self, number, supplier=None):
        return Purchase(supplier=supplier or self.supplier, invoice_number=number, invoice_date=today())

    def test_the_same_invoice_of_the_same_supplier_is_refused(self):
        for number in ('INV-1', 'inv-1', '  INV-1  '):
            with self.subTest(number=number):
                with self.assertRaises(ValidationError) as ctx:
                    self.new(number).full_clean()
                self.assertIn('invoice_number', ctx.exception.message_dict)

    def test_the_same_number_from_another_supplier_is_fine(self):
        other = Supplier.objects.create(company='XYZ Pharma')
        self.new('INV-1', other).full_clean()

    def test_a_new_number_and_re_saving_the_same_purchase_are_fine(self):
        self.new('INV-2').full_clean()
        p = Purchase.objects.get(invoice_number='INV-1')
        p.full_clean()

    def test_the_database_also_refuses_a_duplicate(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Purchase.objects.create(supplier=self.supplier, invoice_number='INV-1', invoice_date=today())


class PurchaseEditStockTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        _, self.item = self.buy(qty=5)                 # 5 Strips = 50 Tablets
        self.sell(qty=40)                              # 40 Tablets sold, 10 left

    def reload(self):
        return Product.objects.get(pk=self.product.pk).stock_qty, Batch.objects.get(purchase_item=self.item).qty

    def test_lowering_the_qty_below_what_was_sold_is_refused_and_nothing_changes(self):
        item = PurchaseItem.objects.get(pk=self.item.pk)
        item.qty = 3                                   # 30 Tablets < 40 already sold
        with self.assertRaises(ValidationError) as ctx:
            item.save()
        self.assertIn('already sold', ' '.join(ctx.exception.messages))
        self.assertEqual(self.reload(), (10, 10))

    def test_lowering_the_qty_down_to_what_was_sold_is_allowed(self):
        item = PurchaseItem.objects.get(pk=self.item.pk)
        item.qty = 4                                   # 40 Tablets = exactly what was sold
        item.save()
        self.assertEqual(self.reload(), (0, 0))

    def test_raising_the_qty_adds_stock(self):
        item = PurchaseItem.objects.get(pk=self.item.pk)
        item.qty = 6
        item.save()
        self.assertEqual(self.reload(), (20, 20))
