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
