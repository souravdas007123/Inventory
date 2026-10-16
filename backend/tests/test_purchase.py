from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from backend.models import Batch, Product, Purchase, PurchaseItem, Supplier, Transaction, Unit

from .helpers import StockMixin, today


class PurchaseTests(StockMixin, TestCase):
    def test_purchase_adds_stock_batch_and_supplier_balance(self):
        _, item = self.buy()                                    # 5 Strip = 50 Tablet, Rs 560
        self.assertEqual(item.unit_factor, 10)
        self.assertEqual(item.base_qty, 50)
        self.assertEqual(self.stock(), 50)
        batch = Batch.objects.get(purchase_item=item)
        self.assertEqual(batch.qty, 50)
        self.assertEqual(batch.product_id, self.product.pk)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.opening_balance, Decimal('560.00'))
        self.assertEqual(Transaction.objects.get(purchase_item=item).purchase_amount, Decimal('560.00'))

    def test_gst_is_taken_out_of_the_price(self):
        _, item = self.buy(price='560.00')                      # 560 incl. 12% GST -> 500 + 60
        self.assertEqual(item.tax, Decimal('500.00'))
        self.assertEqual(item.gst, Decimal('60.00'))
        self.assertEqual(item.cgst + item.sgst, Decimal('60.00'))
        self.assertEqual(item.gst_rate, 12)
        self.assertEqual(item.rate, Decimal('10.0000'))         # 500 / 50 tablets

    def test_selling_price_becomes_the_product_mrp_per_base_unit(self):
        self.buy(selling='100.00')                              # Rs 100 per Strip of 10
        self.assertEqual(Product.objects.get(pk=self.product.pk).mrp, Decimal('10.0000'))

    def test_blank_unit_uses_the_base_unit(self):
        _, item = self.buy(qty=20, price='224.00', unit=None)
        self.assertEqual(item.unit_id, self.tablet.pk)
        self.assertEqual(item.base_qty, 20)
        self.assertEqual(self.stock(), 20)

    def test_unit_without_conversion_is_refused(self):
        box = Unit.objects.create(name='Box')
        with self.assertRaises(ValidationError):
            self.buy(unit=box)
        self.assertEqual(self.stock(), 0)

    def test_editing_the_qty_adjusts_stock_batch_and_balance(self):
        _, item = self.buy()
        item = PurchaseItem.objects.get(pk=item.pk)
        item.qty = 6
        item.purchase_price = Decimal('672.00')
        item.save()
        self.assertEqual(self.stock(), 60)
        self.assertEqual(Batch.objects.get(purchase_item=item).qty, 60)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.opening_balance, Decimal('672.00'))

    def test_deleting_the_purchase_reverses_everything(self):
        purchase, item = self.buy()
        purchase.delete()
        self.assertEqual(self.stock(), 0)
        self.assertFalse(Batch.objects.exists())
        self.assertFalse(Transaction.objects.filter(purchase_item__isnull=False).exists())
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.opening_balance, Decimal('0.00'))

    def test_purchase_that_was_sold_from_cannot_be_deleted(self):
        purchase, _ = self.buy()
        self.sell(qty=45)
        with self.assertRaises(ValidationError):
            purchase.delete()
        self.assertTrue(Purchase.objects.filter(pk=purchase.pk).exists())
        self.assertEqual(self.stock(), 5)

    def test_same_supplier_invoice_number_is_refused_whatever_the_case(self):
        self.buy(invoice='Inv-9')
        dup = Purchase(supplier=self.supplier, invoice_number=' INV-9 ', invoice_date=today())
        with self.assertRaises(ValidationError):
            dup.clean()

    def test_same_invoice_number_is_fine_for_another_supplier(self):
        self.buy(invoice='INV-9')
        other = Supplier.objects.create(company='XYZ Medico')
        Purchase(supplier=other, invoice_number='INV-9', invoice_date=today()).clean()

    def test_qty_and_price_must_be_valid(self):
        purchase, _ = self.buy()
        with self.assertRaises(ValidationError):
            PurchaseItem(purchase=purchase, product=self.product, qty=0, purchase_price=Decimal('10')).clean()
        with self.assertRaises(ValidationError):
            PurchaseItem(purchase=purchase, product=self.product, qty=1, purchase_price=None).clean()
        with self.assertRaises(ValidationError):
            PurchaseItem(purchase=purchase, product=self.product, qty=1, purchase_price=Decimal('-1')).clean()

    def test_expiry_before_manufacture_is_refused(self):
        purchase, _ = self.buy()
        item = PurchaseItem(purchase=purchase, product=self.product, qty=1, purchase_price=Decimal('10'),
                            manufacture_date=today(), expire_date=today() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            item.clean()
