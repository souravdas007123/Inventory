"""Report calculations: ek hi jagah, taaki web page aur admin dono same number dikhayein."""
from decimal import Decimal

from django.db.models import Sum

from .models import Batch, Expense, InvoiceItem, PurchaseItem, Sale


def dec(x):
    return Decimal(str(x or 0))


def profit_and_loss(month, year):
    """
    Income  = Sales ki taxable value (GST income nahi hai)
    COGS    = jo maal bika uski purchase cost (batch_history se)
    Purchases = sirf information ke liye (is mahine kharida hua taxable maal)
    """
    sales = dec(Sale.objects.filter(date__month=month, date__year=year)
                .aggregate(s=Sum('taxable_value'))['s'])

    histories = list(InvoiceItem.objects.filter(sale__date__month=month, sale__date__year=year)
                     .values_list('batch_history', flat=True))
    batch_ids = {int(k) for h in histories for k in (h or {})}
    batch_rate = dict(Batch.objects.filter(pk__in=batch_ids, purchase_item__isnull=False)
                      .values_list('pk', 'purchase_item__rate'))
    cogs = Decimal('0')
    for history in histories:
        for batch_id, qty in (history or {}).items():
            cogs += dec(batch_rate.get(int(batch_id))) * qty

    purchases = dec(PurchaseItem.objects.filter(
        purchase__invoice_date__month=month, purchase__invoice_date__year=year
    ).aggregate(s=Sum('tax'))['s'])
    expenses = dec(Expense.objects.filter(date__month=month, date__year=year)
                   .aggregate(s=Sum('amount'))['s'])

    gross = sales - cogs
    return dict(sales=sales, cogs=cogs, gross=gross, purchases=purchases,
                expenses=expenses, net=gross - expenses)
