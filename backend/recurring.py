"""
Fixed (recurring) expenses and employee salaries that are added automatically.

An item (rent, internet, an employee's salary ...) has a day of the month (1-28). From its start date on, one
expense entry is created for every month whose day has arrived. Which months were already handled is remembered on the
item itself (`last_period`), so an entry that the user deleted on purpose is never created again, and entries that
were missed (the app was not opened for a while) are caught up, at most `CAP` months at a time.

The month arithmetic has no Django imports, so it can be tested on its own.
"""
from datetime import date

CAP = 24


def month_first(d):
    return d.replace(day=1)


def add_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def due_periods(start, end, day, last_period, today, cap=CAP):
    """[(first day of the month, posting date)], oldest first.

    start / end : first and last date the item is valid (end may be None)
    day         : day of the month it is posted on (1-28)
    last_period : first day of the last month already handled (None if nothing yet)
    """
    day = min(max(int(day or 1), 1), 28)
    period = month_first(start)
    if last_period:
        period = max(period, add_month(month_first(last_period)))
    out = []
    while len(out) < cap:
        post = period.replace(day=day)
        if post > today or (end and post > end):
            break
        if post >= start:
            out.append((period, post))
        period = add_month(period)
    return out


def next_post(start, end, day, last_period):
    """Date of the next entry that has not been added yet (it may already be due), or None when finished."""
    found = due_periods(start, end, day, last_period, date(9999, 12, 31), cap=1)
    return found[0][1] if found else None


def _save(expense, owner, attr):
    from django.db import IntegrityError, transaction
    try:
        with transaction.atomic():
            expense.save()
            made = 1
    except IntegrityError:            # the same entry was created a moment ago by another request
        made = 0
    setattr(owner, attr, expense.period)
    owner.save(update_fields=[attr])
    return made


def post_due(today=None):
    """Creates every expense entry that is due. Safe to call as often as you like. Returns how many were added."""
    from django.utils import timezone
    from .models import Employee, Expense, RecurringExpense
    today = today or timezone.localdate()
    made = 0
    for r in RecurringExpense.objects.filter(is_active=True, amount__gt=0):
        for period, post in due_periods(r.start_date, r.end_date, r.day_of_month, r.last_period, today):
            made += _save(Expense(name=r.name, date=post, amount=r.amount, recurring=r, period=period, auto=True,
                                  note=r.title), r, 'last_period')
    for e in Employee.objects.filter(is_active=True, auto_salary=True, monthly_salary__gt=0):
        for period, post in due_periods(e.joining_date, e.leaving_date, e.pay_day, e.last_salary_period, today):
            made += _save(Expense(name='SALARY', date=post, amount=e.monthly_salary, employee=e, period=period,
                                  auto=True, note=f"Salary {period:%b %Y}"), e, 'last_salary_period')
    return made
