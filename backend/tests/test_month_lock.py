"""Month lock: bills and purchases of a closed period cannot be added, changed, cancelled or deleted."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from backend import locks
from backend.models import AuditLog, Bill, InvoiceItem, Purchase, PurchaseItem, ShopProfile

from .helpers import StockMixin, today


class LockTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy(qty=10, invoice='NEW-1')                                  # today: plenty of stock
        self.old = today() - timedelta(days=40)
        self.old_bill = self.new_bill(date=self.old)
        _, self.old_item = self.sell(qty=1, bill=self.old_bill)
        self.old_purchase = Purchase.objects.create(supplier=self.supplier, invoice_number='OLD-1', invoice_date=self.old)
        self.old_pitem = PurchaseItem.objects.create(
            purchase=self.old_purchase, product=self.product, qty=1, unit=self.strip, purchase_price=Decimal('112.00'),
            expire_date=today() + timedelta(days=300))

    def close(self, day):
        shop = ShopProfile.get()
        shop.books_closed_until = day
        shop.save()

    def test_nothing_is_blocked_while_no_date_is_set(self):
        self.old_bill.customer_name = 'Changed'
        self.old_bill.save()
        self.old_purchase.invoice_number = 'OLD-1A'
        self.old_purchase.save()

    def test_a_closed_bill_cannot_be_edited_cancelled_or_deleted(self):
        self.close(today() - timedelta(days=30))
        stock_before = self.stock()
        bill = Bill.objects.get(pk=self.old_bill.pk)
        bill.customer_name = 'Changed'
        with self.assertRaises(ValidationError) as ctx:
            bill.save()
        self.assertIn('closed period', ' '.join(ctx.exception.messages))
        with self.assertRaises(ValidationError):
            bill.cancel()
        with self.assertRaises(ValidationError):
            bill.delete()
        with self.assertRaises(ValidationError):
            bill.clean()
        item = InvoiceItem.objects.get(pk=self.old_item.pk)
        item.qty = 2
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            item.delete()
        self.assertEqual(Bill.objects.get(pk=bill.pk).status, 'FINAL')
        self.assertEqual(self.stock(), stock_before)                       # nothing moved

    def test_a_new_bill_cannot_be_dated_in_a_closed_period_but_today_is_fine(self):
        self.close(today() - timedelta(days=30))
        with self.assertRaises(ValidationError):
            self.new_bill(date=self.old)
        self.new_bill(date=today())

    def test_a_bill_cannot_be_moved_out_of_a_closed_period(self):
        self.close(today() - timedelta(days=30))
        bill = Bill.objects.get(pk=self.old_bill.pk)
        bill.date = today()
        with self.assertRaises(ValidationError):
            bill.save()

    def test_a_closed_purchase_cannot_be_edited_or_deleted(self):
        self.close(today() - timedelta(days=30))
        purchase = Purchase.objects.get(pk=self.old_purchase.pk)
        purchase.invoice_number = 'OLD-1A'
        with self.assertRaises(ValidationError):
            purchase.save()
        with self.assertRaises(ValidationError):
            purchase.clean()
        self.assertIn('closed period', purchase.delete_problem())
        with self.assertRaises(ValidationError):
            purchase.delete()
        item = PurchaseItem.objects.get(pk=self.old_pitem.pk)
        item.qty = 2
        with self.assertRaises(ValidationError):
            item.save()
        self.assertIn('closed period', item.delete_problem())
        with self.assertRaises(ValidationError):
            item.delete()

    def test_the_closing_day_itself_is_closed_and_the_next_day_is_open(self):
        self.close(self.old)
        with self.assertRaises(ValidationError):
            locks.check('This bill', self.old)
        locks.check('This bill', self.old + timedelta(days=1))

    def test_reopening_the_period_allows_the_change_again(self):
        self.close(today() - timedelta(days=30))
        self.close(None)
        bill = Bill.objects.get(pk=self.old_bill.pk)
        bill.customer_name = 'Changed'
        bill.save()
        bill.cancel()

    def test_the_date_cannot_be_in_the_future(self):
        shop = ShopProfile.get()
        shop.books_closed_until = today() + timedelta(days=1)
        with self.assertRaises(ValidationError) as ctx:
            shop.full_clean()
        self.assertIn('books_closed_until', ctx.exception.message_dict)
        shop.books_closed_until = today()
        shop.full_clean()

    def test_the_excel_import_refuses_a_purchase_in_a_closed_period(self):
        from backend import imports
        self.close(today() - timedelta(days=30))
        row = dict(supplier='ABC Pharma', invoice_number='IMP-1', product='Dolo 650', qty='1', purchase_price='100',
                   exp_date=f"31/12/{today().year + 2}", invoice_date=self.old.strftime('%d/%m/%Y'))
        res = imports.IMPORTERS['stock']['validate']([(2, row)], {})
        self.assertEqual(res[0]['action'], 'error')
        self.assertIn('closed period', ' '.join(res[0]['errors']))

    def stock(self):
        from backend.models import Product
        return Product.objects.get(pk=self.product.pk).stock_qty


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class ShopPageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)

    def data(self, **kw):
        d = dict(name='My Pharmacy', address='', phone='', alert_phone='', email='', gstin='', drug_license='',
                 books_closed_until='', terms='x')
        d.update(kw)
        return d

    def test_the_owner_can_close_and_reopen_and_it_is_logged(self):
        day = today() - timedelta(days=3)
        r = self.client.post('/shop/', self.data(books_closed_until=day.isoformat()))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ShopProfile.get().books_closed_until, day)
        self.client.post('/shop/', self.data())
        self.assertIsNone(ShopProfile.get().books_closed_until)
        self.assertEqual(AuditLog.objects.filter(action='LOCK').count(), 2)

    def test_a_future_date_is_refused_on_the_page(self):
        r = self.client.post('/shop/', self.data(books_closed_until=(today() + timedelta(days=2)).isoformat()))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'cannot be in the future')
