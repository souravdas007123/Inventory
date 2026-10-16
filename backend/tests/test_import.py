from decimal import Decimal

from django.test import TestCase

from backend import imports
from backend.models import Batch, Product, ProductUnit, Purchase, PurchaseItem, Supplier, Unit

from .helpers import StockMixin, today

EXP = f"31/12/{today().year + 2}"          # an expiry date that is always in the future


def row(n=2, **kw):
    d = dict(supplier='ABC Pharma', invoice_number='S-1', product='Dolo 650', qty='10', purchase_price='100',
             exp_date=EXP)
    d.update(kw)
    return n, d


class StockImportTests(StockMixin, TestCase):
    imp = imports.IMPORTERS['stock']

    def validate(self, rows, **opts):
        return self.imp['validate'](rows, opts)

    def commit(self, results, **opts):
        return self.imp['commit'](results, opts)

    def errors(self, result):
        return ' | '.join(result['errors'])

    def test_a_valid_row_creates_the_purchase_stock_and_batch(self):
        res = self.validate([row()])
        self.assertEqual(res[0]['action'], 'create', self.errors(res[0]))
        summary = self.commit(res)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['failed'], [])
        purchase = Purchase.objects.get(invoice_number='S-1')
        self.assertEqual(purchase.supplier_id, self.supplier.pk)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(Batch.objects.get(purchase_item__purchase=purchase).qty, 10)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.opening_balance, Decimal('100.00'))

    def test_qty_in_a_larger_unit_is_converted(self):
        res = self.validate([row(unit='Strip', qty='2', purchase_price='224')])
        self.assertEqual(res[0]['action'], 'create', self.errors(res[0]))
        self.commit(res)
        self.assertEqual(self.stock(), 20)

    def test_unknown_supplier_is_an_error(self):
        res = self.validate([row(supplier='Nobody Ltd')])
        self.assertEqual(res[0]['action'], 'error')
        self.assertIn('not found', self.errors(res[0]))

    def test_required_values_are_checked(self):
        for bad in (dict(invoice_number=''), dict(qty='0'), dict(qty='abc'), dict(purchase_price=''), dict(exp_date=''),
                    dict(exp_date='not a date'), dict(selling_price='-5')):
            with self.subTest(bad=bad):
                res = self.validate([row(**bad)])
                self.assertEqual(res[0]['action'], 'error')

    def test_unknown_product_is_an_error_unless_new_products_are_allowed(self):
        res = self.validate([row(product='Azee 500')])
        self.assertEqual(res[0]['action'], 'error')
        res = self.validate([row(product='Azee 500')], create_products=True)         # no gst_rate / base_unit
        self.assertEqual(res[0]['action'], 'error')
        self.assertIn('gst_rate', self.errors(res[0]))

    def test_new_product_is_created_with_the_purchase(self):
        res = self.validate([row(product='Azee 500', gst_rate='12', base_unit='Tablet', composition='Azithromycin 500mg',
                                 hsn_code='3004', brand='Alembic', category='Antibiotic')], create_products=True)
        self.assertEqual(res[0]['action'], 'create', self.errors(res[0]))
        summary = self.commit(res, create_products=True)
        self.assertEqual(summary['created'], 1)
        product = Product.objects.get(name='Azee 500')
        self.assertEqual(product.gst_rate, Decimal('12.00'))
        self.assertEqual(product.unit_id, self.tablet.pk)
        self.assertEqual(product.composition, 'Azithromycin 500mg')
        self.assertEqual(product.stock_qty, 10)
        self.assertEqual(Unit.objects.filter(name__iexact='tablet').count(), 1)

    def test_new_product_with_a_pack_unit(self):
        res = self.validate([row(product='Azee 500', gst_rate='12', base_unit='Tablet', pack_unit='Strip',
                                 pack_size='15', unit='Strip', qty='2', purchase_price='224')], create_products=True)
        self.assertEqual(res[0]['action'], 'create', self.errors(res[0]))
        self.commit(res, create_products=True)
        product = Product.objects.get(name='Azee 500')
        self.assertEqual(product.stock_qty, 30)
        self.assertEqual(ProductUnit.objects.get(product=product, unit=self.strip).factor, 15)

    def test_bad_gst_rate_for_a_new_product_is_an_error(self):
        res = self.validate([row(product='Azee 500', gst_rate='7', base_unit='Tablet')], create_products=True)
        self.assertEqual(res[0]['action'], 'error')

    def test_one_bad_row_skips_the_whole_invoice(self):
        res = self.validate([row(2), row(3, qty='abc')])
        self.assertEqual([r['action'] for r in res], ['error', 'error'])
        summary = self.commit(res)
        self.assertEqual(summary['created'], 0)
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(self.stock(), 0)

    def test_a_good_invoice_is_imported_even_if_another_is_bad(self):
        res = self.validate([row(2, invoice_number='GOOD'), row(3, invoice_number='BAD', qty='0')])
        self.assertEqual([r['action'] for r in res], ['create', 'error'])
        self.commit(res)
        self.assertEqual(list(Purchase.objects.values_list('invoice_number', flat=True)), ['GOOD'])

    def test_one_invoice_with_two_medicines_is_one_purchase(self):
        res = self.validate([row(2), row(3, product='Azee 500', gst_rate='12', base_unit='Tablet')], create_products=True)
        self.assertEqual([r['action'] for r in res], ['create', 'create'], [self.errors(r) for r in res])
        self.commit(res, create_products=True)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(PurchaseItem.objects.count(), 2)

    def test_an_invoice_entered_twice_is_refused(self):
        self.commit(self.validate([row()]))
        res = self.validate([row()])
        self.assertEqual(res[0]['action'], 'error')
        self.assertIn('already been entered', self.errors(res[0]))

    def test_one_invoice_cannot_have_two_dates(self):
        res = self.validate([row(2, invoice_date='01/01/2026'), row(3, invoice_date='02/01/2026')])
        self.assertTrue(all(r['action'] == 'error' for r in res))

    def test_future_invoice_date_is_refused(self):
        res = self.validate([row(invoice_date=f"01/01/{today().year + 1}")])
        self.assertEqual(res[0]['action'], 'error')

    def test_product_can_be_found_by_barcode(self):
        self.product.barcode = '8901234567890'
        self.product.save()
        res = self.validate([row(product='8901234567890')])
        self.assertEqual(res[0]['action'], 'create', self.errors(res[0]))

    def test_an_expired_batch_is_only_a_warning(self):
        res = self.validate([row(exp_date='31/01/2020')])
        self.assertEqual(res[0]['action'], 'create')
        self.assertTrue(any('expired' in w for w in res[0]['warnings']))

    def test_unknown_unit_is_an_error(self):
        res = self.validate([row(unit='Carton')])
        self.assertEqual(res[0]['action'], 'error')

    def test_a_second_import_of_the_same_file_changes_nothing(self):
        rows = [row(2), row(3, product='Azee 500', gst_rate='12', base_unit='Tablet')]
        self.commit(self.validate(rows, create_products=True), create_products=True)
        res = self.validate(rows, create_products=True)
        self.assertTrue(all(r['action'] == 'error' for r in res))
        self.commit(res, create_products=True)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(Supplier.objects.count(), 1)


class ColumnNameTests(TestCase):
    imp = imports.IMPORTERS['stock']

    def test_common_supplier_headers_are_recognised(self):
        headers = ['party', 'bill_no', 'item', 'quantity', 'amount', 'expiry', 'mrp', 'batch_no']
        mapping = imports.canonical_columns(self.imp, headers)
        self.assertEqual(mapping['party'], 'supplier')
        self.assertEqual(mapping['bill_no'], 'invoice_number')
        self.assertEqual(mapping['item'], 'product')
        self.assertEqual(mapping['quantity'], 'qty')
        self.assertEqual(mapping['amount'], 'purchase_price')
        self.assertEqual(mapping['expiry'], 'exp_date')
        self.assertEqual(mapping['mrp'], 'selling_price')
        self.assertEqual(mapping['batch_no'], 'batch')

    def test_missing_required_columns_are_reported(self):
        _, missing, ignored = imports.prepare_rows(self.imp, ['party', 'qty', 'colour'], [(2, {'party': 'A', 'qty': '1', 'colour': 'red'})])
        self.assertIn('invoice_number', missing)
        self.assertIn('product', missing)
        self.assertEqual(ignored, ['colour'])

    def test_the_user_can_choose_a_column_by_hand(self):
        mapped, missing, _ = imports.prepare_rows(
            self.imp, ['goods'], [(2, {'goods': 'Dolo'})], extra={'goods': 'product'})
        self.assertEqual(mapped[0][1], {'product': 'Dolo'})
        self.assertNotIn('product', missing)

    def test_every_template_can_be_downloaded(self):
        for kind in imports.IMPORTERS:
            with self.subTest(kind=kind):
                resp = imports.template_download(kind)
                self.assertEqual(resp.status_code, 200)
                self.assertIn('spreadsheetml', resp['Content-Type'])
                self.assertGreater(len(resp.content), 1000)
