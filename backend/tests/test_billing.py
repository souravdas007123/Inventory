from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from backend.models import Batch, Bill, Customer, CustomerPayment, InvoiceItem, Sale

from .helpers import StockMixin, today


class BillingTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        _, self.pitem = self.buy()                              # 50 Tablets in stock, expiry in a year

    def test_sale_takes_stock_from_the_batch_and_works_out_gst(self):
        bill, item = self.sell(qty=10, rate='10.00')            # 100 + 12% GST
        self.assertEqual(self.stock(), 40)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 40)
        self.assertEqual(item.taxable_value, Decimal('100.00'))
        self.assertEqual(item.gst, Decimal('12.00'))
        self.assertEqual(item.cgst, Decimal('6.00'))
        self.assertEqual(item.sgst, Decimal('6.00'))
        self.assertEqual(item.total, Decimal('112.00'))
        self.assertEqual(Sale.objects.get(invoice_item=item).total, Decimal('112.00'))
        self.assertEqual(bill.grand_total, Decimal('112'))

    def test_igst_bill_has_no_cgst_sgst(self):
        bill = self.new_bill()
        item = InvoiceItem.objects.create(bill=bill, product=self.product, qty=10, rate=Decimal('10.00'), is_igst=True)
        self.assertEqual(item.igst, Decimal('12.00'))
        self.assertEqual(item.cgst + item.sgst, Decimal('0'))

    def test_discount_percent_and_flat(self):
        bill = self.new_bill()
        pct = InvoiceItem.objects.create(bill=bill, product=self.product, qty=10, rate=Decimal('10.00'),
                                         discount_percent=Decimal('10'))
        self.assertEqual(pct.taxable_value, Decimal('90.00'))
        self.assertEqual(pct.gst, Decimal('10.80'))
        flat = InvoiceItem.objects.create(bill=bill, product=self.product, qty=10, rate=Decimal('10.00'),
                                          discount_amount=Decimal('20'))
        self.assertEqual(flat.taxable_value, Decimal('80.00'))

    def test_both_discounts_together_are_refused(self):
        item = InvoiceItem(bill=self.new_bill(), product=self.product, qty=1, rate=Decimal('10'),
                           discount_percent=Decimal('5'), discount_amount=Decimal('1'))
        with self.assertRaises(ValidationError):
            item.clean()

    def test_selling_in_a_larger_unit_converts_to_base_units(self):
        _, item = self.sell(qty=2, rate='100.00', unit=self.strip)   # 2 Strips = 20 Tablets
        self.assertEqual(item.base_qty, 20)
        self.assertEqual(item.total, Decimal('224.00'))
        self.assertEqual(self.stock(), 30)

    def test_more_than_the_stock_cannot_be_sold(self):
        with self.assertRaises(ValidationError):
            self.sell(qty=51)
        self.assertEqual(self.stock(), 50)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 50)

    def test_form_check_shows_insufficient_stock(self):
        item = InvoiceItem(bill=self.new_bill(), product=self.product, qty=51, rate=Decimal('10'))
        with self.assertRaises(ValidationError):
            item.clean()

    def test_expired_stock_is_never_sold(self):
        self.product.batches.update(expire_date=today() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.sell(qty=5)
        self.assertEqual(self.stock(), 50)

    def test_earliest_expiry_is_sold_first(self):
        _, early = self.buy(qty=10, price='100.00', unit='tablet', days=30, invoice='EARLY')
        self.sell(qty=5)
        self.assertEqual(Batch.objects.get(purchase_item=early).qty, 5)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 50)

    def test_a_sale_can_run_over_two_batches(self):
        _, second = self.buy(qty=10, price='100.00', unit='tablet', days=400, invoice='SECOND')
        _, item = self.sell(qty=55)                              # 50 from the first batch + 5 from the second
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 0)
        self.assertEqual(Batch.objects.get(purchase_item=second).qty, 5)
        self.assertEqual(sum(item.batch_history.values()), 55)

    def test_invoice_numbers_are_sequential_and_unique(self):
        a, _ = self.sell(qty=1)
        b, _ = self.sell(qty=1)
        self.assertNotEqual(a.invoice_number, b.invoice_number)
        self.assertTrue(a.invoice_number.endswith('/0001'), a.invoice_number)
        self.assertTrue(b.invoice_number.endswith('/0002'), b.invoice_number)

    def test_cancelling_a_bill_brings_the_stock_back(self):
        bill, item = self.sell(qty=10)
        bill.cancel()
        bill.refresh_from_db()
        self.assertEqual(bill.status, 'CANCELLED')
        self.assertEqual(self.stock(), 50)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 50)
        self.assertFalse(Sale.objects.filter(invoice_item=item).exists())
        bill.cancel()                                            # cancelling again changes nothing
        self.assertEqual(self.stock(), 50)

    def test_deleting_an_item_brings_the_stock_back(self):
        _, item = self.sell(qty=10)
        item.delete()
        self.assertEqual(self.stock(), 50)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 50)

    def test_editing_the_qty_of_an_item_adjusts_the_stock(self):
        _, item = self.sell(qty=10)
        item = InvoiceItem.objects.get(pk=item.pk)
        item.qty = 15
        item.save()
        self.assertEqual(self.stock(), 35)
        item = InvoiceItem.objects.get(pk=item.pk)
        item.qty = 4
        item.save()
        self.assertEqual(self.stock(), 46)
        self.assertEqual(Batch.objects.get(purchase_item=self.pitem).qty, 46)

    def test_item_without_a_qty_is_refused(self):
        with self.assertRaises(ValidationError):
            InvoiceItem.objects.create(bill=self.new_bill(), product=self.product, qty=0, rate=Decimal('10'))


class CreditTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy()
        self.customer = Customer.objects.create(name='Ravi', phone='9876543210')

    def credit_bill(self, qty=10, paid='0.00'):
        bill = self.new_bill(customer=self.customer, payment_mode='CREDIT', amount_paid=Decimal(paid))
        self.sell(qty=qty, bill=bill)
        return bill

    def test_credit_bill_needs_a_customer(self):
        with self.assertRaises(ValidationError):
            Bill(customer_name='Cash', payment_mode='CREDIT', date=today()).clean()

    def test_balance_is_the_unpaid_part_minus_receipts(self):
        self.credit_bill(qty=10, paid='12.00')                   # bill Rs 112, paid 12 now
        self.assertEqual(self.customer.balance(), Decimal('100.00'))
        CustomerPayment.objects.create(customer=self.customer, amount=Decimal('40.00'))
        self.assertEqual(self.customer.balance(), Decimal('60.00'))

    def test_receipt_cannot_be_more_than_the_balance(self):
        self.credit_bill(qty=10)
        with self.assertRaises(ValidationError):
            CustomerPayment(customer=self.customer, amount=Decimal('500.00')).clean()

    def test_cancelled_credit_bill_is_not_owed(self):
        bill = self.credit_bill(qty=10)
        bill.cancel()
        self.assertEqual(self.customer.balance(), Decimal('0'))

    def test_credit_limit_is_enforced(self):
        self.customer.credit_limit = Decimal('100.00')
        self.customer.save()
        bill = self.credit_bill(qty=10)                          # Rs 112 > limit Rs 100
        with self.assertRaises(ValidationError):
            bill.after_items_saved()

    def test_no_limit_means_unlimited(self):
        bill = self.credit_bill(qty=10)
        bill.after_items_saved()                                 # limit 0 = no limit: no error
