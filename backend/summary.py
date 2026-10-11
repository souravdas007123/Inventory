"""End-of-day summary (sales, collections, purchases, stock alerts) as plain text for email / WhatsApp / console."""
from decimal import Decimal

from django.db.models import Sum

from . import notify
from .models import Bill, Customer, CustomerPayment, Expense, InvoiceItem, PurchaseItem, Sale, ShopProfile

ZERO = Decimal('0')


def _rs(x):
    return f"₹{Decimal(x or 0):,.2f}"


def daily_summary(day):
    """Numbers for one day. Sales are net of returns (returns are negative Sale rows)."""
    bills = list(Bill.objects.filter(date=day, status='FINAL').prefetch_related('items'))
    by_mode = {}
    for b in bills:
        by_mode[b.payment_mode] = by_mode.get(b.payment_mode, ZERO) + (b.grand_total or ZERO)
    credit_given = sum((b.credit_due for b in bills), ZERO)
    top = (InvoiceItem.objects.filter(bill__date=day, bill__status='FINAL', product__isnull=False)
           .values('product__name').annotate(t=Sum('total'), q=Sum('qty')).order_by('-t')[:5])
    alerts = notify.collect_alerts()
    outstanding = sum((c.balance() for c in Customer.objects.filter(is_active=True)), ZERO)
    return dict(
        day=day, bills=len(bills),
        sales=Sale.objects.filter(date=day).aggregate(s=Sum('total'))['s'] or ZERO,
        by_mode=by_mode, credit_given=credit_given,
        collected=CustomerPayment.objects.filter(date=day).aggregate(s=Sum('amount'))['s'] or ZERO,
        purchases=PurchaseItem.objects.filter(purchase__invoice_date=day).aggregate(s=Sum('purchase_price'))['s'] or ZERO,
        expenses=Expense.objects.filter(date=day).aggregate(s=Sum('amount'))['s'] or ZERO,
        top=[(r['product__name'] or '-', r['q'], r['t']) for r in top],
        out=len(alerts['out']), low=len(alerts['low']), expiring=len(alerts['expiring']), expired=len(alerts['expired']),
        outstanding=outstanding)


def summary_text(s, shop_name=''):
    lines = [f"{shop_name or 'MedSync'} - Daily summary ({s['day']:%d %b %Y})", '',
             f"Sales: {_rs(s['sales'])} ({s['bills']} bill{'s' if s['bills'] != 1 else ''})"]
    labels = dict(Bill.PAYMENT_CHOICES)
    for mode, amount in sorted(s['by_mode'].items()):
        lines.append(f"  - {str(labels.get(mode, mode)).split(' (')[0]}: {_rs(amount)}")
    lines += [f"Credit given today: {_rs(s['credit_given'])}",
              f"Credit collected today: {_rs(s['collected'])}",
              f"Purchases: {_rs(s['purchases'])}",
              f"Expenses: {_rs(s['expenses'])}", '']
    if s['top']:
        lines.append('Top sellers')
        lines += [f"  - {name} ({qty}) {_rs(total)}" for name, qty, total in s['top']]
        lines.append('')
    lines += [f"Total credit outstanding: {_rs(s['outstanding'])}",
              f"Stock: {s['out']} out of stock, {s['low']} low, {s['expiring']} expiring soon, {s['expired']} expired"]
    return '\n'.join(lines)


def current_text(day):
    return summary_text(daily_summary(day), ShopProfile.get().name)
