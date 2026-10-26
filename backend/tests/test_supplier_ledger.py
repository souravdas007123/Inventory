"""Supplier balance: purchases add to it, payments and returns reduce it, and the ledger page proves it."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Payment, Supplier
from backend.models_extra import AuditLog, PurchaseReturn, PurchaseReturnItem
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class SupplierBalanceTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xx')
        self.client.force_login(self.owner)

    def owed(self):
        return Supplier.objects.get(pk=self.supplier.pk).opening_balance

    def three_purchases(self):
        for n in range(3):
            self.buy(qty=1, price='1000.00', invoice=f'INV-{n}')

    def test_three_purchases_of_1000_then_a_payment_of_1000_leaves_2000(self):
        self.three_purchases()
        self.assertEqual(self.owed(), Decimal('3000.00'))
        self.assertContains(self.client.get('/suppliers/'), '3,000.00')
        pay = Payment.objects.create(supplier=self.supplier, amount=Decimal('1000.00'), payment_mode='CASH')
        self.assertEqual(self.owed(), Decimal('2000.00'))
        self.assertEqual(pay.due, Decimal('2000.00'))                        # "Due after" on the Payments page
        self.assertContains(self.client.get('/suppliers/'), '2,000.00')
        self.assertNotContains(self.client.get('/suppliers/'), '3,000.00')
        page = self.client.get('/payments/')
        self.assertContains(page, '2,000.00')

    def test_a_payment_through_the_form_does_the_same(self):
        self.three_purchases()
        resp = self.client.post('/payments/new/', {'supplier': self.supplier.pk, 'amount': '1000', 'payment_mode': 'CASH'}, follow=True)
        self.assertContains(resp, 'saved')
        self.assertEqual(self.owed(), Decimal('2000.00'))
        self.assertEqual(Payment.objects.get().due, Decimal('2000.00'))

    def test_editing_or_deleting_a_payment_moves_the_balance(self):
        self.three_purchases()
        pay = Payment.objects.create(supplier=self.supplier, amount=Decimal('1000.00'), payment_mode='CASH')
        pay.amount = Decimal('1500.00')
        pay.save()
        self.assertEqual(self.owed(), Decimal('1500.00'))
        pay.delete()
        self.assertEqual(self.owed(), Decimal('3000.00'))

    def test_the_ledger_page_shows_every_entry_and_the_running_balance(self):
        self.three_purchases()
        Payment.objects.create(supplier=self.supplier, amount=Decimal('1000.00'), payment_mode='BANK')
        page = self.client.get(f'/suppliers/{self.supplier.pk}/ledger/')
        self.assertContains(page, 'Purchase INV-0')
        self.assertContains(page, 'Payment (BANK)')
        self.assertContains(page, '₹2000.00')
        self.assertNotContains(page, 'is different from')
        self.assertContains(self.client.get('/suppliers/'), f'/suppliers/{self.supplier.pk}/ledger/')
        self.assertContains(self.client.get('/suppliers/'), f'/payments/new/?supplier={self.supplier.pk}')

    def test_a_return_reduces_the_balance_in_the_ledger_too(self):
        purchase, item = self.buy(qty=5, price='560.00', invoice='INV-R')
        pr = PurchaseReturn.objects.create(purchase=purchase, date=today())
        PurchaseReturnItem.objects.create(purchase_return=pr, purchase_item=item, qty=2)
        page = self.client.get(f'/suppliers/{self.supplier.pk}/ledger/')
        self.assertContains(page, 'Return DN/')
        self.assertContains(page, '₹336.00')
        self.assertNotContains(page, 'is different from')

    def test_a_wrong_saved_balance_is_shown_and_the_owner_can_correct_it(self):
        self.three_purchases()
        Supplier.objects.filter(pk=self.supplier.pk).update(opening_balance=Decimal('4100.00'))        # simulate old bad data
        page = self.client.get(f'/suppliers/{self.supplier.pk}/ledger/')
        self.assertContains(page, 'is different from')
        self.assertContains(page, 'Correct the saved balance')
        resp = self.client.post(f'/suppliers/{self.supplier.pk}/ledger/', follow=True)
        self.assertContains(resp, 'was corrected to')
        self.assertEqual(self.owed(), Decimal('3000.00'))
        self.assertTrue(AuditLog.objects.filter(action='FIX').exists())
        self.assertNotContains(self.client.get(f'/suppliers/{self.supplier.pk}/ledger/'), 'is different from')

    def test_only_the_owner_can_correct_the_balance(self):
        self.three_purchases()
        Supplier.objects.filter(pk=self.supplier.pk).update(opening_balance=Decimal('1.00'))
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345-xx')
        from django.contrib.auth.models import Permission
        clerk.user_permissions.add(Permission.objects.get(codename='view_supplier'))
        self.client.force_login(clerk)
        self.assertEqual(self.client.post(f'/suppliers/{self.supplier.pk}/ledger/').status_code, 403)
        self.assertEqual(self.owed(), Decimal('1.00'))
