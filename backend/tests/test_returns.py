from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from backend.models import Batch, Sale, Supplier
from backend.models_extra import PurchaseReturn, PurchaseReturnItem, SalesReturn, SalesReturnItem

from .helpers import StockMixin, today


class SalesReturnTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        _, self.pitem = self.buy()
        self.bill, self.item = self.sell(qty=10, rate='10.00')   # Rs 100 + 12 GST = 112; stock 40

    def make_return(self, qty, mode='CASH'):
        sr = SalesReturn.objects.create(bill=self.bill, refund_mode=mode, date=today())
        return sr, SalesReturnItem.objects.create(sales_return=sr, invoice_item=self.item, qty=qty)

    def test_return_puts_the_goods_back_and_reverses_the_sale(self):
        sr, ri = self.make_return(4)                             # 4 of 10 -> taxable 40, GST 4.80
        self.assertEqual(ri.taxable_value, Decimal('40.00'))
        self.assertEqual(ri.gst, Decimal('4.80'))
        self.assertEqual(ri.cgst + ri.sgst, Decimal('4.80'))
        self.assertEqual(ri.total, Decimal('44.80'))
        self.assertEqual(self.stock(), 44)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 44)
        self.assertEqual(Sale.objects.get(sales_return_item=ri).total, Decimal('-44.80'))
        self.assertTrue(sr.note_number.startswith('CN/'), sr.note_number)

    def test_more_than_was_sold_cannot_be_returned(self):
        with self.assertRaises(ValidationError):
            self.make_return(11)
        self.assertEqual(self.stock(), 40)

    def test_returns_add_up_to_the_item_without_losing_paise(self):
        bill, item = self.sell(qty=10, rate='33.33')             # taxable 333.30, GST 40.00, total 373.30
        sr = SalesReturn.objects.create(bill=bill, date=today())
        first = SalesReturnItem.objects.create(sales_return=sr, invoice_item=item, qty=3)
        last = SalesReturnItem.objects.create(sales_return=sr, invoice_item=item, qty=7)
        self.assertEqual(first.total + last.total, item.total)
        self.assertEqual(first.taxable_value + last.taxable_value, item.taxable_value)
        with self.assertRaises(ValidationError):                 # everything is returned already
            SalesReturnItem.objects.create(sales_return=sr, invoice_item=item, qty=1)

    def test_a_return_item_cannot_be_edited(self):
        _, ri = self.make_return(2)
        ri.qty = 3
        with self.assertRaises(ValidationError):
            ri.save()

    def test_deleting_the_return_takes_the_goods_out_again(self):
        sr, _ = self.make_return(4)
        sr.delete()
        self.assertEqual(self.stock(), 40)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 40)
        self.assertFalse(Sale.objects.filter(total__lt=0).exists())

    def test_a_bill_with_a_return_cannot_be_cancelled(self):
        self.make_return(1)
        with self.assertRaises(ValidationError):
            self.bill.cancel()

    def test_an_item_with_a_return_cannot_be_edited(self):
        self.make_return(1)
        item = type(self.item).objects.get(pk=self.item.pk)
        item.qty = 12
        with self.assertRaises(ValidationError):
            item.save()

    def test_return_for_a_cancelled_bill_is_refused(self):
        self.bill.cancel()
        with self.assertRaises(ValidationError):
            SalesReturn(bill=self.bill, date=today()).clean()

    def test_adjusting_against_credit_needs_a_customer(self):
        with self.assertRaises(ValidationError):
            SalesReturn(bill=self.bill, refund_mode='ADJUST', date=today()).clean()


class PurchaseReturnTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.purchase, self.pitem = self.buy()                   # 5 Strip, Rs 560 -> stock 50, supplier owed 560

    def make_return(self, qty):
        pr = PurchaseReturn.objects.create(purchase=self.purchase, date=today())
        return pr, PurchaseReturnItem.objects.create(purchase_return=pr, purchase_item=self.pitem, qty=qty)

    def owed(self):
        return Supplier.objects.get(pk=self.supplier.pk).opening_balance

    def test_return_takes_stock_out_and_reduces_what_is_owed(self):
        pr, ri = self.make_return(2)                             # 2 of 5 Strips
        self.assertEqual(ri.base_qty, 20)
        self.assertEqual(ri.amount, Decimal('224.00'))
        self.assertEqual(ri.taxable_value, Decimal('200.00'))
        self.assertEqual(ri.gst, Decimal('24.00'))
        self.assertEqual(self.stock(), 30)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 30)
        self.assertEqual(self.owed(), Decimal('336.00'))
        self.assertTrue(pr.note_number.startswith('DN/'), pr.note_number)

    def test_returns_add_up_to_the_purchase(self):
        _, first = self.make_return(2)
        _, last = self.make_return(3)
        self.assertEqual(first.amount + last.amount, Decimal('560.00'))
        self.assertEqual(self.owed(), Decimal('0.00'))
        self.assertEqual(self.stock(), 0)

    def test_more_than_was_bought_cannot_be_returned(self):
        with self.assertRaises(ValidationError):
            self.make_return(6)
        self.assertEqual(self.stock(), 50)

    def test_goods_that_were_already_sold_cannot_be_returned(self):
        self.sell(qty=45, rate='1.00')                           # only 5 Tablets are left
        with self.assertRaises(ValidationError):
            self.make_return(1)                                  # 1 Strip = 10 Tablets
        self.assertEqual(self.stock(), 5)
        self.assertEqual(self.owed(), Decimal('560.00'))

    def test_deleting_the_return_puts_everything_back(self):
        pr, _ = self.make_return(2)
        pr.delete()
        self.assertEqual(self.stock(), 50)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 50)
        self.assertEqual(self.owed(), Decimal('560.00'))

    def test_a_return_item_cannot_be_edited(self):
        _, ri = self.make_return(1)
        ri.qty = 2
        with self.assertRaises(ValidationError):
            ri.save()

    def test_an_item_with_a_return_cannot_be_edited(self):
        self.make_return(1)
        item = type(self.pitem).objects.get(pk=self.pitem.pk)
        item.qty = 6
        with self.assertRaises(ValidationError):
            item.save()
