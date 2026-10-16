"""Tests of the plain calculations (no database needed)."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from backend import billview
from backend.tabular import to_date, to_dec, to_int

TODAY = date(2026, 10, 16)


class ReadingValuesTests(SimpleTestCase):
    def test_dates_in_the_usual_formats(self):
        for text in ('31/08/2027', '2027-08-31', '31-08-2027', '31-08-27'):
            errors = []
            self.assertEqual(to_date(text, 'd', errors), date(2027, 8, 31), text)
            self.assertEqual(errors, [])

    def test_month_only_expiry_means_the_last_day_of_the_month(self):
        for text in ('08/27', '08/2027', 'Aug-27'):
            errors = []
            self.assertEqual(to_date(text, 'exp', errors, month_end=True), date(2027, 8, 31), text)
            self.assertEqual(errors, [])

    def test_bad_and_missing_dates(self):
        errors = []
        self.assertIsNone(to_date('not a date', 'exp', errors))
        self.assertEqual(len(errors), 1)
        errors = []
        self.assertIsNone(to_date('', 'exp', errors, required=True))
        self.assertEqual(len(errors), 1)
        errors = []
        self.assertIsNone(to_date('', 'exp', errors))
        self.assertEqual(errors, [])

    def test_numbers(self):
        errors = []
        self.assertEqual(to_dec('1,250.50', 'p', errors), Decimal('1250.50'))
        self.assertEqual(to_int('12', 'q', errors), 12)
        self.assertEqual(errors, [])
        for bad in ('abc', ''):
            errors = []
            self.assertIsNone(to_dec(bad, 'p', errors, required=True))
            self.assertEqual(len(errors), 1)
        errors = []
        self.assertIsNone(to_int('1.5', 'q', errors))
        self.assertEqual(len(errors), 1)
        errors = []
        self.assertIsNone(to_dec('-1', 'p', errors, minimum=Decimal('0')))
        self.assertEqual(len(errors), 1)


def invoice_item(**kw):
    d = dict(batch_history={'1': 5}, rate=Decimal('10'), qty=5, taxable_value=Decimal('45'), gst_rate=Decimal('12.00'),
             total=Decimal('50.40'), cgst=Decimal('2.70'), sgst=Decimal('2.70'), igst=Decimal('0'),
             product=NS(name='Dolo 650', composition='Paracetamol'), product_id=1, unit='Strip')
    d.update(kw)
    return NS(**d)


def bill(**kw):
    d = dict(payment_mode='CASH', amount_paid=Decimal('0'), display_number='INV/2026-27/0001', date=TODAY,
             customer_name='Ravi', customer_id=None, status='FINAL', get_payment_mode_display=lambda: 'Cash',
             doctor_name='', patient_name='')
    d.update(kw)
    return NS(**d)


class BillPreviewTests(SimpleTestCase):
    batches = {1: NS(batch_number='B1', expire_date=date(2027, 3, 1))}

    def test_items_and_totals(self):
        d = billview.preview_data(bill(), [invoice_item()], self.batches, '/invoice/1/')
        item = d['items'][0]
        self.assertEqual(item['batch'], 'B1 / 03-27')
        self.assertEqual(item['qty'], '5 Strip')
        self.assertEqual(item['disc'], '₹5.00')          # 5 x 10 = 50 gross, 45 taxable
        self.assertEqual(item['gst'], '12%')
        self.assertEqual(d['totals']['taxable'], '₹45.00')
        self.assertEqual(d['totals']['gst'], '₹5.40')
        self.assertEqual(d['totals']['grand'], '₹50.00')
        self.assertEqual(d['totals']['round_off'], '₹-0.40')
        self.assertFalse(d['credit'])
        self.assertFalse(d['cancelled'])

    def test_rounding_is_half_up(self):
        d = billview.preview_data(bill(), [invoice_item(total=Decimal('50.50'))], self.batches, '/i/')
        self.assertEqual(d['totals']['grand'], '₹51.00')

    def test_credit_bill_shows_paid_and_due(self):
        d = billview.preview_data(bill(payment_mode='CREDIT', amount_paid=Decimal('20')), [invoice_item()],
                                  self.batches, '/i/')
        self.assertTrue(d['credit'])
        self.assertEqual(d['totals']['paid'], '₹20.00')
        self.assertEqual(d['totals']['due'], '₹30.00')

    def test_cancelled_bill_and_missing_batch(self):
        d = billview.preview_data(bill(status='CANCELLED'), [invoice_item(batch_history={'9': 1})], self.batches, '/i/')
        self.assertTrue(d['cancelled'])
        self.assertEqual(d['items'][0]['batch'], '-')

    def test_a_bill_without_items(self):
        d = billview.preview_data(bill(), [], {}, '/i/')
        self.assertEqual(d['count'], 0)
        self.assertEqual(d['totals']['grand'], '₹0.00')


def purchase_item(expire, **kw):
    d = dict(expire_date=expire, purchase_price=Decimal('315'), qty=10, composition='', unit='Strip', batch='B1',
             product=NS(name='Dolo 650', composition='Paracetamol'), product_id=1, selling_price=Decimal('40'),
             gst_rate=12, tax=Decimal('281.25'), gst=Decimal('33.75'))
    d.update(kw)
    return NS(**d)


class PurchasePreviewTests(SimpleTestCase):
    purchase = NS(invoice_number='S-1', invoice_date=TODAY, supplier=NS(company='ABC Pharma'))

    def preview(self, *items):
        return billview.purchase_preview_data(self.purchase, list(items), TODAY, '/edit/', '/return/')

    def test_totals_and_rate_per_unit(self):
        d = self.preview(purchase_item(TODAY + timedelta(days=365)))
        item = d['items'][0]
        self.assertEqual(item['rate'], '₹31.50')
        self.assertEqual(item['mrp'], '₹40.00')
        self.assertEqual(item['exp'], TODAY.replace(year=2027).strftime('%m-%y'))
        self.assertEqual(d['totals'], dict(taxable='₹281.25', gst='₹33.75', total='₹315.00'))
        self.assertEqual((d['edit_url'], d['return_url']), ('/edit/', '/return/'))

    def test_short_expiry_is_flagged(self):
        d = self.preview(purchase_item(TODAY + timedelta(days=365)), purchase_item(TODAY + timedelta(days=90)),
                         purchase_item(TODAY + timedelta(days=91)), purchase_item(TODAY - timedelta(days=1)),
                         purchase_item(None))
        self.assertEqual([i['flag'] for i in d['items']], ['', 'soon', '', 'expired', ''])
        self.assertEqual(d['short'], 2)
        self.assertEqual(d['items'][4]['exp'], '-')

    def test_zero_qty_does_not_crash(self):
        d = self.preview(purchase_item(None, qty=0, purchase_price=Decimal('0')))
        self.assertEqual(d['items'][0]['rate'], '-')
