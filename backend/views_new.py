"""Newer pages: Fast / slow / dead stock, Payment reminders, Medicine finder and a customer's buying history."""
from datetime import timedelta
from decimal import Decimal

from django.shortcuts import render

from . import access, movement
from .imports import table_download


SHOW_MAX = 500


@access.owner_required
def movement_view(request):
    days = movement.period_from(request.GET.get('days'))
    data = movement.build(days)
    shown = request.GET.get('show', '')
    if shown not in movement.CLASSES:
        shown = ''
    rows = [r for r in data['rows'] if not shown or r['cls'] == shown]
    if request.GET.get('export') in ('xlsx', 'csv'):
        heads = ['Medicine', 'Class', 'Stock', f'Sold in {days} days (base units)', 'Stock lasts (days)', 'Last sold', 'Stock value (cost)', 'Nearest expiry']
        body = [[r['name'], r['label'], r['stock'], r['sold'], r['cover'] if r['cover'] is not None else '',
                 f"{r['last_sold']:%d/%m/%Y}" if r['last_sold'] else '', f"{r['value']:.2f}" if r['value'] is not None else '',
                 f"{r['expiry']:%d/%m/%Y}" if r['expiry'] else ''] for r in rows]
        return table_download(f'stock-movement-{days}d', heads, body, request.GET['export'])
    tied = data['totals']['dead']['value'] + data['totals']['slow']['value']
    found = len(rows)
    rows = rows[:SHOW_MAX]                 # a page of thousands of rows is slow to draw; the Excel file has them all
    return render(request, 'app/movement.html', dict(
        active='movement', rows=rows, found=found, show_max=SHOW_MAX, totals=data['totals'], days=days, periods=movement.PERIODS, shown=shown,
        classes=[(c, movement.LABELS[c]) for c in movement.CLASSES],
        cards=[dict(key=c, label=movement.LABELS[c], **data['totals'][c]) for c in movement.CLASSES], tied=tied, fast_cover=movement.FAST_COVER, slow_cover=movement.SLOW_COVER))


# ---------------- Payment reminders ----------------
from django.contrib import messages  # noqa: E402
from django.http import HttpResponseRedirect  # noqa: E402
from django.shortcuts import get_object_or_404, redirect  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.views.decorators.http import require_POST  # noqa: E402

from . import notify, reminders  # noqa: E402
from .models import Customer, ShopProfile  # noqa: E402


@access.perm_required(('view', 'customer'))
def reminders_view(request):
    min_days = reminders.days_from(request.GET.get('days'))
    skip_recent = request.GET.get('skip') == '1'
    rows = [r for r in reminders.due_customers() if r['days'] >= min_days and not (skip_recent and r['recent'])]
    return render(request, 'app/reminders.html', dict(
        active='reminders', rows=rows, total=sum((r['balance'] for r in rows), Decimal('0')), min_days=min_days, skip=skip_recent,
        choices=reminders.DAYS_CHOICES, recent_days=reminders.RECENT, no_phone=sum(1 for r in rows if not r['phone_ok'])))


@require_POST
@access.perm_required(('view', 'customer'))
def reminder_send(request, pk):
    """Notes that a reminder was sent today, then opens WhatsApp with the message already written (nothing is sent by the server)."""
    customer = get_object_or_404(Customer, pk=pk)
    balance = customer.balance()
    if balance <= 0:
        messages.info(request, f'{customer.name} owes nothing now.')
        return redirect('reminders')
    digits = ''.join(ch for ch in (customer.phone or '') if ch.isdecimal())
    if len(digits) < 10:
        messages.error(request, f'{customer.name} has no phone number. Add one in Customers first.')
        return redirect('reminders')
    Customer.objects.filter(pk=customer.pk).update(last_reminded=timezone.localdate())
    return HttpResponseRedirect(notify.credit_reminder_link(ShopProfile.get().name, customer.name, customer.phone, balance))


# ---------------- A customer's buying history ----------------
from django.db.models import Count, Max, Q  # noqa: E402
from django.http import JsonResponse  # noqa: E402

from .models import Bill, InvoiceItem  # noqa: E402
from .views_extra import _db_id  # noqa: E402

HISTORY_BILLS = 5
HISTORY_ITEMS = 8


def _unit_name(item):
    if item.unit_id:
        return item.unit.name
    return item.product.unit.name if item.product_id and item.product.unit_id else ''


@access.perm_required(('add', 'bill'), ('change', 'bill'))
def customer_history(request):
    """Billing helper: what this customer bought before. The last few bills, and the medicines they buy most often."""
    cid = _db_id(request.GET.get('id', ''))
    customer = Customer.objects.filter(pk=cid).first() if cid else None
    if customer is None:
        return JsonResponse({'found': False})
    bills = list(Bill.objects.filter(customer=customer, status='FINAL').order_by('-date', '-id')[:HISTORY_BILLS]
                 .prefetch_related('items__product__unit', 'items__unit'))
    out = []
    for b in bills:
        items = [i for i in b.items.all() if i.product_id]
        out.append(dict(id=b.pk, number=b.display_number, date=b.date.strftime('%d %b %Y') if b.date else '', total=str(b.grand_total),
                        items=[dict(pid=i.product_id, name=i.product.name or str(i.product), qty=i.qty or 0, unit=_unit_name(i))
                               for i in items[:HISTORY_ITEMS]], more=max(len(items) - HISTORY_ITEMS, 0)))
    since = timezone.localdate() - timedelta(days=365)
    usual = list(InvoiceItem.objects.filter(bill__customer=customer, bill__status='FINAL', bill__date__gte=since, product__isnull=False,
                                            product__is_active=True)
                 .values('product_id', 'product__name').annotate(times=Count('bill_id', distinct=True), last=Max('bill__date'))
                 .order_by('-times', '-last')[:5])
    return JsonResponse(dict(found=True, bills=out, usual=[dict(pid=u['product_id'], name=u['product__name'] or '', times=u['times'],
                                                                last=u['last'].strftime('%d %b %Y') if u['last'] else '') for u in usual]))


@access.perm_required(('view', 'bill'))
def history_view(request):
    """Find past bills by customer (name or phone), patient or doctor, and see which medicines were given."""
    cust, patient, doctor = (request.GET.get(k, '').strip()[:100] for k in ('customer', 'patient', 'doctor'))
    qs = Bill.objects.filter(status='FINAL')
    if cust:
        digits = ''.join(ch for ch in cust if ch.isdecimal())
        cond = Q(customer_name__icontains=cust) | Q(customer__name__icontains=cust)
        if len(digits) >= 6:
            cond |= Q(customer__phone__contains=digits)
        qs = qs.filter(cond)
    if patient:
        qs = qs.filter(patient_name__icontains=patient)
    if doctor:
        qs = qs.filter(doctor_name__icontains=doctor)
    searched = bool(cust or patient or doctor)
    rows = []
    if searched:
        for b in qs.order_by('-date', '-id').select_related('customer').prefetch_related('items__product')[:100]:
            names = [i.product.name for i in b.items.all() if i.product_id and i.product.name]
            rows.append(dict(bill=b, meds=', '.join(names[:6]) + (f' + {len(names) - 6} more' if len(names) > 6 else '')))
    return render(request, 'app/history.html', dict(active='history', rows=rows, searched=searched, cust=cust, patient=patient, doctor=doctor,
                                                    capped=len(rows) == 100))


# ---------------- Medicine Finder ----------------
from . import finder  # noqa: E402


@access.perm_required(('view', 'product'))
def finder_view(request):
    q = request.GET.get('q', '').strip()[:100]
    return render(request, 'app/finder.html', dict(active='finder', q=q, results=finder.find(q), searched=bool(q)))
