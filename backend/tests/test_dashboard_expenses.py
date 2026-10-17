"""Expenses and net profit on the dashboard: the same figures as the Profit & Loss page, owner only."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import Batch, Expense, StockWriteOff
from backend.reports import dashboard_overview, expense_overview, profit_and_loss

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


@override_settings(**SAFE)
class DashboardExpenseTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.buy(qty=5)
        self.sell(qty=10, rate='10.00')
        Expense.objects.create(name='RENT', amount=Decimal('100.00'), date=today())
        Expense.objects.create(name='ELECTRICITY', amount=Decimal('40.00'), date=today())
        Expense.objects.create(name='RENT', amount=Decimal('5.00'), date=today().replace(day=1) - timedelta(days=1))   # last month: ignored
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')

    def test_expenses_today_and_this_month(self):
        ov = dashboard_overview(today(), include_money=True)
        self.assertTrue(ov['exp'])
        self.assertEqual(ov['expenses_today'], Decimal('140.00'))
        self.assertEqual(ov['expenses_month'], Decimal('140.00'))

    def test_expenses_by_type_biggest_first(self):
        rows = dashboard_overview(today(), include_money=True)['expense_rows']
        self.assertEqual([(r['label'], r['amount'], r['pct']) for r in rows],
                         [('Rent', Decimal('100.00'), 100), ('Electricity', Decimal('40.00'), 40)])

    def test_net_profit_is_the_same_as_on_the_profit_and_loss_page(self):
        batch = Batch.objects.filter(product=self.product, qty__gt=0).first()
        StockWriteOff.objects.create(batch=batch, qty=3, reason='DAMAGED', date=today())
        ov = dashboard_overview(today(), include_money=True)
        pl = profit_and_loss(today().month, today().year)
        self.assertEqual(ov['net_month'], pl['net'])
        self.assertEqual(ov['expenses_month'], pl['expenses'])
        self.assertEqual(ov['writeoffs_month'], pl['writeoffs'])
        self.assertGreater(ov['writeoffs_month'], 0)

    def test_nothing_is_shown_without_money_rights(self):
        ov = dashboard_overview(today(), include_money=False)
        self.assertNotIn('exp', ov)
        self.assertNotIn('net_month', ov)

    def test_no_expenses_gives_zeroes_and_an_empty_list(self):
        Expense.objects.all().delete()
        data = expense_overview(today(), Decimal('50.00'))
        self.assertEqual((data['expenses_month'], data['net_month'], data['expense_rows']), (0, Decimal('50.00'), []))

    def test_the_owner_sees_the_cards_and_the_breakdown(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Expenses (today / this month)')
        self.assertContains(resp, 'Net profit this month')
        self.assertContains(resp, 'Expenses this month')
        self.assertContains(resp, 'Electricity')

    def test_a_failure_in_the_expense_part_never_breaks_the_dashboard(self):
        from unittest import mock
        self.client.force_login(self.owner)
        with mock.patch('backend.reports.expense_overview', side_effect=RuntimeError('boom')):
            resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Net profit this month')
        self.assertContains(resp, 'Gross profit this month')

    def test_staff_without_owner_rights_do_not_see_expenses(self):
        user = get_user_model().objects.create_user('clerk', password='pw-12345')
        from django.contrib.auth.models import Permission
        user.is_staff = True
        user.save()
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='backend', codename__in=['view_sale', 'view_bill']))
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Net profit this month')
        self.assertNotContains(resp, 'Expenses (today / this month)')

    def test_the_wording_has_no_hinglish(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'bika')
        self.assertEqual(dict(StockWriteOff.REASONS)['DAMAGED'], 'Damaged / broken')
