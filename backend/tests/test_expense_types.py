"""By hand the Expenses form offers Salary and Other; Rent, Electricity ... belong to Fixed Expenses."""
import re
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Employee, Expense, RecurringExpense

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


def options(html, field):
    block = re.search(r'<select[^>]*name="%s".*?</select>' % field, html, re.S).group(0)
    return re.findall(r'<option value="([^"]*)"(?: selected)?>', block), re.findall(r'<option value="([^"]*)" selected', block)


@override_settings(**SAFE)
class ExpenseTypeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))

    def test_a_new_expense_offers_only_other_and_salary_and_starts_on_other(self):
        opts, chosen = options(self.client.get('/expenses/new/').content.decode(), 'name')
        self.assertEqual(sorted(opts), ['OTHER', 'SALARY'])
        self.assertEqual(chosen, ['OTHER'])

    def test_fixed_expenses_keep_the_whole_list(self):
        opts, _ = options(self.client.get('/fixed_expenses/new/').content.decode(), 'name')
        for k in ('RENT', 'ELECTRICITY', 'WATER', 'MAINTENANCE', 'OTHER'):
            self.assertIn(k, opts)

    def test_a_one_time_expense_can_be_saved_as_other(self):
        resp = self.client.post('/expenses/new/', dict(name='OTHER', amount='250', date='2026-10-01', note='Tea for staff'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Expense.objects.get().name, 'OTHER')

    def test_rent_cannot_be_typed_in_by_hand_any_more(self):
        resp = self.client.post('/expenses/new/', dict(name='RENT', amount='250', date='2026-10-01'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Expense.objects.exists())

    def test_an_automatic_rent_entry_can_still_be_opened_and_corrected(self):
        rent = RecurringExpense.objects.create(title='Shop rent', name='RENT', amount=1000, day_of_month=5)
        e = Expense.objects.create(name='RENT', amount=Decimal('1000'), date=date(2026, 10, 5), recurring=rent,
                                   period=date(2026, 10, 1), auto=True)
        opts, chosen = options(self.client.get(f'/expenses/{e.pk}/').content.decode(), 'name')
        self.assertEqual(sorted(opts), ['OTHER', 'RENT', 'SALARY'])
        self.assertEqual(chosen, ['RENT'])
        resp = self.client.post(f'/expenses/{e.pk}/', dict(name='RENT', amount='1100', date='2026-10-05', note='Raised'))
        self.assertEqual(resp.status_code, 302)
        e.refresh_from_db()
        self.assertEqual((e.name, e.amount), ('RENT', Decimal('1100.00')))

    def test_add_salary_from_the_employee_list_still_works(self):
        emp = Employee.objects.create(name='Asha', monthly_salary=Decimal('12000'))
        html = self.client.get(f'/expenses/new/?employee={emp.pk}').content.decode()
        self.assertEqual(options(html, 'name')[1], ['SALARY'])
        resp = self.client.post('/expenses/new/', dict(name='SALARY', employee=emp.pk, amount='12000', date='2026-10-30'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Expense.objects.get().employee_id, emp.pk)

    def test_the_list_explains_where_fixed_costs_go(self):
        self.assertContains(self.client.get('/expenses/'), 'Fixed Expenses')
