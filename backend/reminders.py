"""Payment reminders: the customers who owe you money, how long it has been due, and a WhatsApp message for each.

The oldest unpaid credit bill is found by applying every receipt (and credit adjustment) to the oldest bills first."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Bill, Customer

DAYS_CHOICES = (0, 7, 15, 30, 60)
RECENT = 7          # "not reminded in the last 7 days"


def days_from(raw):
    return int(raw) if str(raw).isdecimal() and int(raw) in DAYS_CHOICES else 0


def oldest_unpaid(bills, applied):
    """Date of the oldest credit bill that is still (partly) unpaid, when `applied` (receipts + adjustments) is used on the oldest first."""
    left = applied
    for b in bills:
        due = b.credit_due
        if left >= due:
            left -= due
            continue
        return b.date
    return None


def due_customers(today=None):
    """[{'customer', 'balance', 'since', 'days', 'reminded', 'phone_ok'}] for every active customer with a balance above zero."""
    today = today or timezone.localdate()
    credit = (Bill.objects.filter(status='FINAL', payment_mode='CREDIT', customer__isnull=False, customer__is_active=True)
              .select_related('customer').prefetch_related('items').order_by('date', 'id'))
    by_customer = {}
    for b in credit:
        by_customer.setdefault(b.customer_id, []).append(b)
    customers = [bills[0].customer for bills in by_customer.values()]
    Customer.bulk_balances(customers)
    out = []
    for c in customers:
        balance = c._balance
        if balance <= 0:
            continue
        bills = by_customer[c.pk]
        applied = sum((b.credit_due for b in bills), Decimal('0')) - balance
        since = oldest_unpaid(bills, applied) or bills[-1].date
        digits = ''.join(ch for ch in (c.phone or '') if ch.isdecimal())
        out.append(dict(customer=c, balance=balance, since=since, days=(today - since).days if since else 0,
                        reminded=c.last_reminded, phone_ok=len(digits) >= 10,
                        recent=bool(c.last_reminded and (today - c.last_reminded).days < RECENT)))
    out.sort(key=lambda r: (-r['balance'], r['customer'].name.lower()))
    return out
