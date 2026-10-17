"""Deleting a purchase that the rules refuse must show a message, never a Server Error (500) - on the site and in the admin."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import Batch, Product, Purchase
from backend.models_extra import PurchaseReturn, PurchaseReturnItem, StockWriteOff

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'])


def make_return(purchase, item, qty=1):
    pr = PurchaseReturn.objects.create(purchase=purchase, date=today())
    return pr, PurchaseReturnItem.objects.create(purchase_return=pr, purchase_item=item, qty=qty)


@override_settings(**SAFE)
class PurchaseDeleteTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.purchase, self.item = self.buy()                  # 50 tablets
        self.admin_delete = f'/admin/backend/purchase/{self.purchase.pk}/delete/'

    # ---- what the model says, without changing anything ----
    def test_delete_problem_is_empty_for_an_unsold_purchase(self):
        self.assertEqual(self.item.delete_problem(), '')
        self.assertEqual(self.purchase.delete_problem(), '')

    def test_delete_problem_when_part_was_sold(self):
        self.sell(qty=45)
        self.assertIn('already been sold', self.item.delete_problem())
        self.assertIn('Dolo 650', self.purchase.delete_problem())
        self.assertEqual(self.stock(), 5)                      # asking changed nothing

    def test_delete_problem_when_a_purchase_return_exists(self):
        make_return(self.purchase, self.item, 1)
        self.assertIn('cannot be deleted', self.purchase.delete_problem())

    def test_delete_problem_when_part_was_written_off(self):
        StockWriteOff.objects.create(batch=Batch.objects.get(purchase_item=self.item), qty=2, reason='DAMAGED', date=today())
        self.assertIn('already been sold/used/returned', self.item.delete_problem())

    def test_a_refused_delete_raises_a_validation_error_and_changes_nothing(self):
        self.sell(qty=45)
        with self.assertRaises(ValidationError):
            self.purchase.delete()
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())
        self.assertEqual(self.stock(), 5)

    # ---- the site ----
    def test_the_site_shows_a_message_when_the_purchase_was_sold_from(self):
        self.sell(qty=45)
        resp = self.client.post(reverse('delete', kwargs={'key': 'purchases', 'pk': self.purchase.pk}), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already been sold')
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())

    def test_the_site_shows_a_message_when_a_return_exists(self):
        make_return(self.purchase, self.item, 1)
        resp = self.client.post(reverse('delete', kwargs={'key': 'purchases', 'pk': self.purchase.pk}), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'cannot be deleted')
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())

    # ---- the admin ----
    def test_admin_delete_of_a_sold_purchase_shows_a_message_not_a_500(self):
        self.sell(qty=45)
        resp = self.client.post(self.admin_delete, {'post': 'yes'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already been sold')
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())
        self.assertEqual(self.stock(), 5)

    def test_admin_delete_with_a_return_does_not_crash(self):
        make_return(self.purchase, self.item, 1)
        resp = self.client.post(self.admin_delete, {'post': 'yes'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())

    def test_admin_delete_of_an_unsold_purchase_still_works(self):
        resp = self.client.post(self.admin_delete, {'post': 'yes'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Purchase.objects.filter(pk=self.purchase.pk).exists())
        self.assertEqual(self.stock(), 0)

    def test_admin_bulk_delete_skips_the_one_that_cannot_go_and_deletes_the_rest(self):
        self.sell(qty=45)                                     # the first purchase has been sold from
        other, _ = self.buy(invoice='SECOND', unit='tablet', product=Product.objects.create(name='Other 10', unit=self.tablet))
        resp = self.client.post('/admin/backend/purchase/', {
            'action': 'delete_selected', '_selected_action': [self.purchase.pk, other.pk], 'post': 'yes'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already been sold')
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())
        self.assertFalse(Purchase.objects.filter(pk=other.pk).exists())

    def test_an_error_from_a_business_rule_while_saving_in_the_admin_is_a_message(self):
        data = {'supplier': self.supplier.pk, 'invoice_number': 'NEW-1', 'invoice_date': today().isoformat(),
                'items-TOTAL_FORMS': 0, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 0, 'items-MAX_NUM_FORMS': 1000, '_save': 'Save'}
        with mock.patch('backend.admin.PurchaseAdmin.save_model', side_effect=ValidationError('This cannot be saved.')):
            resp = self.client.post('/admin/backend/purchase/add/', data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'This cannot be saved.')
        self.assertFalse(Purchase.objects.filter(invoice_number='NEW-1').exists())
