"""Purchase returns where the supplier keeps a part of the value (expired goods): the debit note stays at the full value,
the supplier balance goes down by the credit only, and the part kept is a loss in the Profit & Loss report."""
import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from backend import ca_export, reports, views_extra
from backend.models import Batch, PurchaseReturn, PurchaseReturnItem, Supplier, Transaction

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
D = Decimal


class Base(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.purchase, self.pitem = self.buy()                   # 5 Strip, Rs 560 incl. GST (taxable 500, GST 60)

    def owed(self):
        return Supplier.objects.get(pk=self.supplier.pk).opening_balance

    def ret(self, qty, **fields):
        override = fields.pop('credit_override', None)
        pr = PurchaseReturn.objects.create(purchase=self.purchase, date=today())
        item = PurchaseReturnItem(purchase_return=pr, purchase_item=self.pitem, qty=qty, **fields)
        item.credit_override = override
        item.save()
        return pr, item


class ModelTests(Base):
    def test_by_default_the_supplier_credits_everything(self):
        pr, it = self.ret(2)
        self.assertEqual((it.amount, it.credit, it.credit_percent, it.deduction), (D('224.00'), D('224.00'), D('100.00'), D('0.00')))
        self.assertEqual(self.owed(), D('336.00'))

    def test_a_percent_changes_only_the_credit_not_the_debit_note(self):
        pr, it = self.ret(5, credit_percent=D('80'))
        self.assertEqual(it.amount, D('560.00'))                 # the debit note: full value
        self.assertEqual(it.taxable_value, D('500.00'))
        self.assertEqual(it.gst, D('60.00'))
        self.assertEqual(it.credit, D('448.00'))
        self.assertEqual(it.deduction, D('112.00'))
        self.assertEqual(self.owed(), D('112.00'))               # 560 owed - 448 credited
        self.assertEqual(self.stock(), 0)                        # the goods are gone either way
        self.assertEqual(Transaction.objects.get(purchase_return_item=it).purchase_amount, D('-448.00'))
        self.assertEqual((pr.total, pr.credit_total, pr.deduction_total), (D('560.00'), D('448.00'), D('112.00')))

    def test_a_rupee_amount_works_and_sets_the_percent(self):
        _, it = self.ret(5, credit_override=D('420'))
        self.assertEqual((it.credit, it.credit_percent), (D('420.00'), D('75.00')))
        self.assertEqual(self.owed(), D('140.00'))

    def test_no_credit_at_all(self):
        _, it = self.ret(5, credit_percent=D('0'))
        self.assertEqual((it.credit, it.deduction), (D('0.00'), D('560.00')))
        self.assertEqual(self.owed(), D('560.00'))               # nothing is taken off; the goods are written off as a loss

    def test_a_tiny_rounding_difference_from_the_screen_is_forgiven_a_big_one_is_not(self):
        _, it = self.ret(2, credit_override=D('224.05'))
        self.assertEqual(it.credit, D('224.00'))
        with self.assertRaises(ValidationError):
            self.ret(2, credit_override=D('225.00'))
        with self.assertRaises(ValidationError):
            self.ret(1, credit_override=D('-1'))

    def test_a_percent_outside_0_to_100_is_refused_and_nothing_changes(self):
        for bad in (D('100.01'), D('-5')):
            with self.assertRaises(ValidationError):
                self.ret(1, credit_percent=bad)
        self.assertEqual(self.stock(), 50)
        self.assertEqual(self.owed(), D('560.00'))

    def test_deleting_the_return_gives_back_exactly_the_credit(self):
        pr, _ = self.ret(5, credit_percent=D('80'))
        pr.delete()
        self.assertEqual(self.owed(), D('560.00'))
        self.assertEqual(self.stock(), 50)

    def test_the_last_part_of_a_line_and_the_credit_add_up(self):
        _, a = self.ret(2, credit_percent=D('50'))
        _, b = self.ret(3, credit_percent=D('50'))
        self.assertEqual(a.amount + b.amount, D('560.00'))
        self.assertEqual(a.credit + b.credit, D('280.00'))
        self.assertEqual(self.owed(), D('280.00'))

    def test_old_returns_keep_the_full_credit_after_the_migration(self):
        _, it = self.ret(2)
        PurchaseReturnItem.objects.filter(pk=it.pk).update(credit=0, credit_percent=D('7'))
        importlib.import_module('backend.migrations.0025_return_credit').full_credit_for_old_returns(django_apps, None)
        it.refresh_from_db()
        self.assertEqual((it.credit, it.credit_percent), (it.amount, D('100.00')))


@override_settings(**SAFE)
class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))
        self.url = '/purchases/%d/return/' % self.purchase.pk

    def post(self, **extra):
        data = {'date': today().isoformat(), 'reason': 'Expired', f'qty_{self.pitem.pk}': '5'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_the_form_shows_the_credit_boxes_and_the_hint(self):
        html = self.client.get(self.url).content.decode()
        for needle in (f'name="credit_pct_{self.pitem.pk}"', f'name="credit_amt_{self.pitem.pk}"', 'Supplier credit (%)',
                       'class="credit-sum', 'creditInit('):
            self.assertIn(needle, html)

    def test_the_sales_return_form_has_none_of_it(self):
        from backend.models import Bill
        bill, _ = self.sell(qty=3)
        html = self.client.get('/bills/%d/return/' % bill.pk).content.decode()
        self.assertNotIn('credit_pct_', html)

    def test_saving_with_a_percent(self):
        r = self.post(**{f'credit_pct_{self.pitem.pk}': '75'})
        self.assertEqual(r.status_code, 302)
        it = PurchaseReturnItem.objects.get()
        self.assertEqual((it.credit, it.credit_percent), (D('420.00'), D('75.00')))
        self.assertEqual(self.owed(), D('140.00'))

    def test_saving_with_a_rupee_amount_that_wins_over_the_percent(self):
        self.post(**{f'credit_pct_{self.pitem.pk}': '100', f'credit_amt_{self.pitem.pk}': '400.50'})
        self.assertEqual(PurchaseReturnItem.objects.get().credit, D('400.50'))

    def test_blank_boxes_mean_full_credit(self):
        self.post()
        self.assertEqual(PurchaseReturnItem.objects.get().credit, D('560.00'))

    def test_bad_input_creates_nothing_and_says_why(self):
        for field, value in (('credit_pct', '150'), ('credit_pct', 'abc'), ('credit_pct', '-1'), ('credit_amt', 'x'),
                             ('credit_amt', '-5'), ('credit_amt', '999999'), ('credit_pct', 'NaN'), ('credit_amt', 'Infinity')):
            r = self.post(**{f'{field}_{self.pitem.pk}': value})
            self.assertEqual(r.status_code, 200, (field, value))
            self.assertContains(r, 'supplier', status_code=200)
            self.assertEqual(PurchaseReturn.objects.count(), 0, (field, value))
            self.assertEqual(self.owed(), D('560.00'), (field, value))
            self.assertEqual(self.stock(), 50, (field, value))

    def test_a_failed_form_shows_the_typed_values_again(self):
        r = self.post(**{f'credit_pct_{self.pitem.pk}': '150'})
        self.assertContains(r, 'value="150"')

    def test_note_list_and_ledger_show_the_deduction(self):
        self.post(**{f'credit_pct_{self.pitem.pk}': '80'})
        pr = PurchaseReturn.objects.get()
        note = self.client.get('/purchase_returns/%d/note/' % pr.pk).content.decode()
        self.assertIn('Credit from supplier', note)
        self.assertIn('112.00', note)
        self.assertIn('560.00', note)                            # the debit note value is unchanged
        lst = self.client.get('/purchase_returns/').content.decode()
        self.assertIn('Kept by supplier', lst)
        ledger = self.client.get('/suppliers/%d/ledger/' % self.supplier.pk) if False else None
        rows, balance = views_extra._supplier_statement(Supplier.objects.get(pk=self.supplier.pk))
        self.assertEqual(balance, D('112.00'))
        self.assertEqual(balance, self.owed())                   # the statement and the stored balance agree
        self.assertIn('supplier kept', rows[-1]['text'])

    def test_a_note_without_a_deduction_has_no_credit_line(self):
        self.post()
        pr = PurchaseReturn.objects.get()
        self.assertNotIn('Credit from supplier', self.client.get('/purchase_returns/%d/note/' % pr.pk).content.decode())


@override_settings(**SAFE)
class ExpiryTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))
        Batch.objects.filter(purchase_item=self.pitem).update(expire_date=today() - timedelta(days=3))
        self.batch = Batch.objects.get(purchase_item=self.pitem)

    def test_the_page_has_a_credit_box_per_batch(self):
        html = self.client.get('/expiry/').content.decode()
        self.assertIn(f'name="credit_pct_{self.batch.pk}"', html)
        self.assertIn(f'name="credit_amt_{self.batch.pk}"', html)
        self.assertIn('data-full="560.00"', html)

    def test_returning_with_a_deduction(self):
        r = self.client.post('/expiry/', {'batch': [self.batch.pk], 'reason': 'Expired', f'credit_pct_{self.batch.pk}': '60'}, follow=True)
        self.assertContains(r, 'keeps')
        it = PurchaseReturnItem.objects.get()
        self.assertEqual((it.amount, it.credit), (D('560.00'), D('336.00')))
        self.assertEqual(self.owed(), D('224.00'))

    def test_without_boxes_it_behaves_as_before(self):
        self.client.post('/expiry/', {'batch': [self.batch.pk], 'reason': 'Expired'})
        self.assertEqual(PurchaseReturnItem.objects.get().credit, D('560.00'))
        self.assertEqual(self.owed(), D('0.00'))

    def test_bad_credit_returns_nothing(self):
        r = self.client.post('/expiry/', {'batch': [self.batch.pk], f'credit_pct_{self.batch.pk}': '101'}, follow=True)
        self.assertContains(r, 'Nothing was returned')
        self.assertEqual(PurchaseReturn.objects.count(), 0)
        self.assertEqual(self.owed(), D('560.00'))
        self.assertEqual(Batch.objects.get(pk=self.batch.pk).qty, 50)

    def test_credit_of_an_unticked_batch_is_ignored(self):
        r = self.client.post('/expiry/', {'reason': 'x', f'credit_pct_{self.batch.pk}': '999'}, follow=True)
        self.assertContains(r, 'Tick at least one batch')


class ProfitTests(Base):
    def test_the_part_kept_by_the_supplier_is_a_loss_in_the_report_and_dashboard(self):
        t = today()
        before = reports.profit_and_loss(t.month, t.year)
        self.assertEqual(before['deductions'], D('0'))
        self.ret(5, credit_percent=D('80'))
        after = reports.profit_and_loss(t.month, t.year)
        self.assertEqual(after['deductions'], D('112.00'))
        self.assertEqual(after['net'], before['net'] - D('112.00'))
        ov = reports.expense_overview(t, after['gross'])
        self.assertEqual(ov['deductions_month'], D('112.00'))
        self.assertEqual(ov['net_month'], after['net'])          # the dashboard and the report agree

    def test_a_full_credit_return_is_not_a_loss(self):
        self.ret(5)
        self.assertEqual(reports.profit_and_loss(today().month, today().year)['deductions'], D('0'))

    def test_the_gst_side_and_the_ca_export_still_use_the_full_debit_note(self):
        self.ret(5, credit_percent=D('50'))
        start = today() - timedelta(days=1)
        rows = ca_export.debit_notes(start, today())
        self.assertEqual((rows[0]['total'], rows[0]['taxable'], rows[0]['gst']), (D('560.00'), D('500.00'), D('60.00')))

    @override_settings(**SAFE)
    def test_the_report_pages_show_the_new_line(self):
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))
        self.ret(5, credit_percent=D('80'))
        page = self.client.get('/report/').content.decode()
        self.assertIn('Kept by suppliers on returns', page)
        self.assertIn('112.00', page)
        dash = self.client.get('/').content.decode()
        self.assertIn('Kept by suppliers on returns', dash)
