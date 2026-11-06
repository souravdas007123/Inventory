"""The Expiry Returns page must show the suppliers, batches and medicines (a menu variable once hid them all), and the return must work."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Batch, PurchaseReturn

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


@override_settings(**SAFE)
class ExpiryPageTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))
        self.purchase, self.pitem = self.buy()                        # 5 Strips = 50 Tablets from ABC Pharma
        Batch.objects.filter(purchase_item=self.pitem).update(expire_date=today() - timedelta(days=3))

    def test_the_page_lists_the_supplier_the_medicine_and_the_value(self):
        resp = self.client.get('/expiry/?days=30')
        self.assertContains(resp, 'ABC Pharma')
        self.assertContains(resp, 'Dolo 650')
        self.assertContains(resp, 'Expired 3 days ago')
        self.assertContains(resp, '5 Strip')
        self.assertContains(resp, '1 supplier ·')
        self.assertContains(resp, 'name="batch"')
        self.assertEqual(resp.context['suppliers'][0]['count'], 1)

    def test_the_side_menu_still_shows_its_groups(self):
        html = self.client.get('/expiry/').content.decode()
        for title in ('Suppliers', 'Customers', 'Accounts'):
            self.assertIn(f'<summary>{title}</summary>', html)

    def test_returning_the_expired_batch_makes_a_debit_note_and_empties_the_batch(self):
        batch = Batch.objects.get(purchase_item=self.pitem)
        resp = self.client.post('/expiry/', {'batch': [batch.pk], 'reason': 'Expired'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PurchaseReturn.objects.count(), 1)
        batch.refresh_from_db()
        self.assertEqual(batch.qty, 0)
        self.assertContains(self.client.get('/expiry/'), 'Nothing is expired')

    def test_nothing_ticked_changes_nothing(self):
        resp = self.client.post('/expiry/', {'reason': 'x'}, follow=True)
        self.assertContains(resp, 'Tick at least one batch')
        self.assertEqual(PurchaseReturn.objects.count(), 0)


@override_settings(**SAFE)
class ExpiryPageLimitTests(StockMixin, TestCase):
    """A page of thousands of rows is of no use: the earliest to expire come first, and the page says so."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))
        self.items = []
        for n in range(4):
            _, item = self.buy(invoice=f'EXP-{n}')
            Batch.objects.filter(purchase_item=item).update(expire_date=today() - timedelta(days=10 - n))     # 10, 9, 8, 7 days ago
            self.items.append(item)

    def test_a_short_list_has_no_notice(self):
        page = self.client.get('/expiry/?days=30')
        self.assertNotContains(page, 'that expire first')
        self.assertEqual(page.context['suppliers'][0]['count'], 4)

    def test_a_long_list_shows_the_earliest_first_and_says_so(self):
        from unittest import mock
        from backend import views_extra
        with mock.patch.object(views_extra, 'EXPIRY_PAGE_MAX', 2):
            page = self.client.get('/expiry/?days=30')
        self.assertContains(page, 'There are 4 batches to deal with. This page shows the 2 that expire first.')
        shown = [i['batch_id'] for inv in page.context['suppliers'][0]['invoices'] for i in inv['items']]
        earliest = list(Batch.objects.filter(purchase_item__in=self.items).order_by('expire_date').values_list('pk', flat=True)[:2])
        self.assertEqual(sorted(shown), sorted(earliest))
        self.assertEqual(page.context['total_due'], 4)

    def test_returning_the_shown_batches_brings_the_next_ones(self):
        from unittest import mock
        from backend import views_extra
        with mock.patch.object(views_extra, 'EXPIRY_PAGE_MAX', 2):
            first = self.client.get('/expiry/?days=30')
            ids = [i['batch_id'] for inv in first.context['suppliers'][0]['invoices'] for i in inv['items']]
            self.assertEqual(self.client.post('/expiry/', {'batch': ids, 'reason': 'Expired'}).status_code, 302)
            again = self.client.get('/expiry/?days=30')
        left = [i['batch_id'] for inv in again.context['suppliers'][0]['invoices'] for i in inv['items']]
        self.assertEqual(len(left), 2)
        self.assertFalse(set(left) & set(ids))
