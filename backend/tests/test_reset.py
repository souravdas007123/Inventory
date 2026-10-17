from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from backend import backup, reset
from backend.models import (AuditLog, Batch, Bill, Brand, Category, Customer, CustomerPayment, Employee, Expense, Payment,
                            Product, Purchase, RecurringExpense, ShopProfile, Supplier, Unit)

from .helpers import StockMixin, today


class ResetTests(StockMixin, TestCase):
    def fill(self):
        self.buy(qty=5)                                              # purchase + batch + stock
        customer = Customer.objects.create(name='Ravi')
        self.sell(qty=10)                                            # bill with one item
        CustomerPayment.objects.create(customer=customer, amount=5, date=today())
        Payment.objects.create(supplier=self.supplier, amount=100, payment_mode='CASH')
        Expense.objects.create(name='RENT', amount=500, date=today())
        Employee.objects.create(name='Asha')
        RecurringExpense.objects.create(title='Rent', name='RENT', amount=1000, day_of_month=5)
        Category.objects.create(name='Tablet')
        Brand.objects.create(name='Cipla')

    def test_transactions_only_keeps_products_and_parties(self):
        self.fill()
        reset.run(['transactions'])
        self.assertEqual((Bill.objects.count(), Purchase.objects.count(), Batch.objects.count(), Expense.objects.count()), (0, 0, 0, 0))
        self.assertEqual(Payment.objects.count() + CustomerPayment.objects.count(), 0)
        self.assertEqual(self.stock(), 0)                            # stock follows the batches
        self.assertEqual(Supplier.objects.get(pk=self.supplier.pk).opening_balance, 0)      # nothing is owed any more
        self.assertEqual((Product.objects.count(), Supplier.objects.count(), Customer.objects.count(), Employee.objects.count()), (1, 1, 1, 1))

    def test_everything_leaves_a_clean_start(self):
        self.fill()
        done = reset.run(list(reset.KEYS))
        self.assertEqual(done, list(reset.KEYS))
        for model in (Bill, Purchase, Batch, Expense, Product, Supplier, Customer, Employee, RecurringExpense, Category, Brand):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        self.assertEqual(set(Unit.objects.values_list('name', flat=True)), set(reset.DEFAULT_UNITS))

    def test_choosing_medicines_also_removes_the_records_that_depend_on_them(self):
        self.fill()
        self.assertEqual(reset.normalise(['medicines']), ['transactions', 'medicines'])
        self.assertEqual(reset.normalise(['nonsense']), [])
        reset.run(['medicines'])
        self.assertEqual((Product.objects.count(), Bill.objects.count(), Purchase.objects.count()), (0, 0, 0))
        self.assertEqual(Supplier.objects.count(), 1)

    def test_it_works_on_an_empty_database_and_can_run_twice(self):
        reset.run(list(reset.KEYS))
        reset.run(list(reset.KEYS))
        self.assertEqual(Product.objects.count(), 0)

    def test_new_data_can_be_entered_after_a_reset(self):
        self.fill()
        reset.run(list(reset.KEYS))
        sup = Supplier.objects.create(company='New Distributor')
        unit = Unit.objects.get(name='Tablet')
        prod = Product.objects.create(name='Crocin', unit=unit)
        self.supplier, self.product, self.tablet = sup, prod, unit
        self.buy(qty=20, unit='tablet', invoice='N-1')
        self.assertEqual(self.stock(), 20)
        bill, _ = self.sell(qty=5)
        self.assertTrue(bill.display_number)


class ResetViewTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.fake_backup = mock.patch.object(backup, 'make_backup', return_value=Path('medsync_test.sqlite3.gz'))
        self.fake_backup.start()
        self.addCleanup(self.fake_backup.stop)
        ShopProfile.get()

    def post(self, **kw):
        data = {'groups': ['transactions'], 'confirm': 'RESET', 'password': 'pw-12345'}
        data.update(kw)
        return self.client.post(reverse('reset_data'), data)

    def test_page_opens_for_the_owner(self):
        self.assertContains(self.client.get(reverse('reset_data')), 'Reset Data')

    def test_staff_without_owner_rights_cannot_open_or_post(self):
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345', is_staff=True)
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(reverse('reset_data')).status_code, 403)
        self.buy()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_wrong_word_wrong_password_or_nothing_ticked_removes_nothing(self):
        self.buy()
        self.post(confirm='reset')
        self.post(password='wrong')
        self.post(groups=[])
        self.assertEqual(Purchase.objects.count(), 1)

    def test_a_correct_request_removes_the_data_and_is_logged(self):
        self.buy()
        resp = self.post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Purchase.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action='RESET').exists())
        self.assertTrue(get_user_model().objects.filter(username='owner').exists())
        self.assertEqual(ShopProfile.objects.count(), 1)

    def test_nothing_is_removed_when_the_safety_backup_fails(self):
        self.buy()
        with mock.patch.object(backup, 'make_backup', side_effect=backup.BackupError('no disk')):
            self.post()
        self.assertEqual(Purchase.objects.count(), 1)
