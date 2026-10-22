"""Phone number, HSN code, drug licence number and bill rate vs MRP.
An old value already saved is not blocked until it is edited (same rule as the GSTIN check)."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from backend import imports, validators as v
from backend.models import Customer, Employee, InvoiceItem, Product, ShopProfile, Supplier

from .helpers import StockMixin


class PhoneFunctionTests(SimpleTestCase):
    def test_common_ways_of_writing_one_number_give_the_same_10_digits(self):
        for raw in ('9876543210', '98765 43210', '+91 98765 43210', '+91-98765-43210', '091 9876543210',
                    '919876543210', '09876543210', '(98765) 43210'):
            with self.subTest(raw=raw):
                self.assertEqual(v.normalise_phone(raw), '9876543210')
                self.assertEqual(v.phone_problem(raw), '')

    def test_blank_is_fine(self):
        for raw in ('', None, '   ', '-'):
            self.assertEqual(v.phone_problem(raw), '')

    def test_wrong_numbers_are_caught(self):
        for raw in ('12345', '987654321', '98765432100', '5876543210', '0000000000', '+1 202 555 0100'):
            with self.subTest(raw=raw):
                self.assertNotEqual(v.phone_problem(raw), '')

    def test_a_number_starting_below_6_has_its_own_message(self):
        self.assertIn('start with 6', v.phone_problem('5876543210'))

    def test_a_landline_is_accepted_only_where_allowed(self):
        self.assertEqual(v.phone_problem('011 2345 6789', landline=True), '')
        self.assertEqual(v.phone_problem('0484-2345678', landline=True), '')
        self.assertNotEqual(v.phone_problem('011 2345 6789'), '')


class HsnFunctionTests(SimpleTestCase):
    def test_4_6_and_8_digits_are_fine(self):
        for h in ('3004', '300490', '30049099', ' 3004 90 99 ', '3004.90'):
            with self.subTest(h=h):
                self.assertEqual(v.hsn_problem(h), '')

    def test_other_lengths_and_letters_are_caught(self):
        for h in ('300', '30049', '3004909', '300490999', 'ABCD'):
            with self.subTest(h=h):
                self.assertNotEqual(v.hsn_problem(h), '')

    def test_blank_is_fine(self):
        self.assertEqual(v.hsn_problem(''), '')
        self.assertEqual(v.hsn_problem(None), '')


class LicenceFunctionTests(SimpleTestCase):
    def test_usual_formats_pass(self):
        for t in ('20/21/12345', 'DL No. 20/21/12345', '20/21/12345, 21/21/12345', 'KL-TVM-123456', '20B/21B-1234',
                  'Drug Licence No: 12345/A', 'RLF-20/21-5678'):
            with self.subTest(t=t):
                self.assertEqual(v.licence_problem(t), '')

    def test_obvious_mistakes_are_caught(self):
        for t in ('abc', 'none', '123', 'DL No.', '20/21/12345 @@', '20/21/12345, xyz'):
            with self.subTest(t=t):
                self.assertNotEqual(v.licence_problem(t), '')

    def test_blank_is_fine(self):
        self.assertEqual(v.licence_problem(''), '')


class RateAboveMrpFunctionTests(SimpleTestCase):
    def test_rate_is_compared_with_the_mrp_without_gst(self):
        # MRP 112 incl. 12% GST = 100.00 before GST
        self.assertFalse(v.rate_above_mrp('100.00', 12, '112'))
        self.assertFalse(v.rate_above_mrp('80', 12, '112'))
        self.assertTrue(v.rate_above_mrp('101.50', 12, '112'))

    def test_one_paisa_of_rounding_is_allowed(self):
        # MRP 10 incl. 12% GST = 8.9286 -> 8.93 typed by the user
        self.assertFalse(v.rate_above_mrp('8.93', 12, '10'))
        self.assertFalse(v.rate_above_mrp('8.94', 12, '10'))
        self.assertTrue(v.rate_above_mrp('9.00', 12, '10'))

    def test_no_mrp_means_no_check(self):
        self.assertFalse(v.rate_above_mrp('500', 12, None))
        self.assertFalse(v.rate_above_mrp('500', 12, 0))
        self.assertFalse(v.rate_above_mrp(None, 12, '10'))


class ModelPhoneTests(TestCase):
    def test_customer_phone_is_checked_and_cleaned(self):
        c = Customer(name='A', phone='12345')
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn('phone', ctx.exception.message_dict)
        c = Customer(name='A', phone='+91 98765 43210')
        c.full_clean()
        self.assertEqual(c.phone, '9876543210')

    def test_blank_phone_stays_empty(self):
        c = Customer(name='A', phone='')
        c.full_clean()
        self.assertIsNone(c.phone)

    def test_an_old_bad_number_is_not_blocked_until_it_is_edited(self):
        c = Customer.objects.create(name='Old', phone='12345')        # saved before the check existed
        c = Customer.objects.get(pk=c.pk)
        c.full_clean()                                               # unchanged: fine
        c.phone = '54321'
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_employee_and_supplier_phone(self):
        with self.assertRaises(ValidationError):
            Employee(name='E', phone='999').full_clean()
        Employee(name='E', phone='98765 43210').full_clean()
        with self.assertRaises(ValidationError):
            Supplier(company='S', phone='999').full_clean()
        s = Supplier(company='S', phone='0484 2345678')               # a supplier may have a landline
        s.full_clean()
        self.assertEqual(s.phone, '04842345678')

    def test_shop_phone_allows_a_landline_but_the_alert_number_must_be_a_mobile(self):
        ShopProfile(phone='011 2345 6789').full_clean()
        with self.assertRaises(ValidationError) as ctx:
            ShopProfile(alert_phone='011 2345 6789').full_clean()
        self.assertIn('alert_phone', ctx.exception.message_dict)
        shop = ShopProfile(phone='+91 98765 43210', alert_phone='09876543210')
        shop.full_clean()
        self.assertEqual((shop.phone, shop.alert_phone), ('9876543210', '9876543210'))


class ModelHsnAndLicenceTests(TestCase):
    def test_product_hsn_length(self):
        with self.assertRaises(ValidationError) as ctx:
            Product(name='P', hsn_code='30049', gst_rate=Decimal('12.00')).full_clean(exclude=['unit'])
        self.assertIn('hsn_code', ctx.exception.message_dict)
        p = Product(name='P', hsn_code='3004 90 99', gst_rate=Decimal('12.00'))
        p.full_clean(exclude=['unit'])
        self.assertEqual(p.hsn_code, '30049099')

    def test_an_old_hsn_is_not_blocked_until_it_is_edited(self):
        p = Product.objects.create(name='P', hsn_code='30049', gst_rate=Decimal('12.00'))
        p = Product.objects.get(pk=p.pk)
        p.full_clean()
        p.hsn_code = '300'
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_shop_licence(self):
        with self.assertRaises(ValidationError) as ctx:
            ShopProfile(drug_license='abc').full_clean()
        self.assertIn('drug_license', ctx.exception.message_dict)
        ShopProfile(drug_license='DL No. 20/21/12345,  21/21/12345').full_clean()


class ImportValidationTests(TestCase):
    def run_import(self, kind, raw, **opts):
        return imports.IMPORTERS[kind]['validate']([(2, raw)], opts)[0]

    def test_customer_phone(self):
        bad = self.run_import('customers', {'name': 'A', 'phone': '12345'})
        self.assertEqual(bad['action'], 'error')
        self.assertIn('phone', ' '.join(bad['errors']))
        good = self.run_import('customers', {'name': 'A', 'phone': '+91 98765 43210'})
        self.assertEqual(good['action'], 'create', good['errors'])
        self.assertEqual(good['data']['phone'], '9876543210')

    def test_supplier_phone_may_be_a_landline(self):
        good = self.run_import('suppliers', {'company': 'S', 'phone': '0484 2345678'})
        self.assertEqual(good['action'], 'create', good['errors'])
        bad = self.run_import('suppliers', {'company': 'S', 'phone': '999'})
        self.assertEqual(bad['action'], 'error')

    def test_product_hsn(self):
        base = {'name': 'P', 'unit': 'Tablet', 'gst_rate': '12'}
        bad = self.run_import('products', dict(base, hsn_code='30049'))
        self.assertEqual(bad['action'], 'error')
        self.assertIn('hsn_code', ' '.join(bad['errors']))
        good = self.run_import('products', dict(base, hsn_code='3004'))
        self.assertEqual(good['action'], 'create', good['errors'])


class BillRateAboveMrpTests(StockMixin, TestCase):
    """A medicine cannot be billed for more than its MRP. The check is in clean() (forms and admin)."""

    def setUp(self):
        super().setUp()
        self.buy(selling='100.00')                    # Rs 100 per Strip of 10 = MRP Rs 10 per Tablet incl. 12% GST
        self.product.refresh_from_db()

    def item(self, rate, unit=None):
        return InvoiceItem(bill=self.new_bill(), product=self.product, qty=1, rate=Decimal(rate), unit=unit)

    def test_a_rate_within_the_mrp_is_accepted(self):
        self.item('8.93').clean()           # 8.93 + 12% = 10.00
        self.item('5.00').clean()           # a lower rate is always fine

    def test_a_rate_above_the_mrp_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            self.item('12.00').clean()
        self.assertIn('rate', ctx.exception.message_dict)
        self.assertIn('MRP', ' '.join(ctx.exception.message_dict['rate']))

    def test_the_mrp_of_the_chosen_unit_is_used(self):
        self.item('89.30', unit=self.strip).clean()     # Strip MRP 100 -> 89.29 before GST
        with self.assertRaises(ValidationError):
            self.item('95.00', unit=self.strip).clean()

    def test_a_product_without_mrp_is_not_checked(self):
        Product.objects.filter(pk=self.product.pk).update(mrp=None)
        self.product.refresh_from_db()
        self.item('500.00').clean()

    def test_an_old_bill_can_still_be_edited_after_the_mrp_went_down(self):
        _, item = self.sell(qty=1, rate='8.93')
        Product.objects.filter(pk=self.product.pk).update(mrp=Decimal('5.00'))
        item = InvoiceItem.objects.get(pk=item.pk)
        item.product.refresh_from_db()
        item.clean()                        # the rate is unchanged, so it is not checked again
