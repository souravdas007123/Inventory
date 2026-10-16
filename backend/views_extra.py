"""Views for the newer features: customer ledger, returns, write-off, GST/drug reports, barcode lookup,
alerts, Purchase Order share, stock audit."""
import calendar
import json
import re
from datetime import date as date_cls, datetime as datetime_cls, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import access, auditlog, backup, billview, ca_export, health, expiry_returns, levels, notify, refill, reorder, reports, summary, supplier_prices
from .models import (
    Batch, Bill, Brand, Category, Customer, Employee, Expense, InvoiceItem, Product, ProductUnit, Purchase, PurchaseItem, PurchaseOrder,
    HeldBill, PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem, SalesReturn, Supplier, Unit, SalesReturnItem, ShopProfile, StockAudit, StockAuditItem, StockWriteOff,
)
from .imports import GST_ALLOWED, _fk, table_download
from .views import page, post_recurring


def staff_required(view):
    """Owner only (superuser, or staff who may change Shop Settings)."""
    return access.owner_required(view)


def _parse_date(raw, default=None):
    try:
        return date_cls.fromisoformat((raw or '').strip())
    except ValueError:
        return default or timezone.localdate()


def _month_year(request):
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    if not 1 <= month <= 12:
        month = today.month
    return today, month, year


def _month_ctx(today, month, year):
    return dict(month=month, year=year, month_name=calendar.month_name[month],
                months=[(i, calendar.month_name[i]) for i in range(1, 13)],
                years=list(range(today.year - 5, today.year + 1)))


def _xlsx(content, filename):
    resp = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


# ======================================================
# CUSTOMER LEDGER
# ======================================================

@access.perm_required(('view', 'customer'))
def customer_ledger(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    entries = []
    for b in customer.bills.filter(status='FINAL', payment_mode='CREDIT').prefetch_related('items'):
        entries.append((b.date, 0, f"Credit bill {b.display_number}", b.credit_due, Decimal('0')))
    for p in customer.payments.all():
        entries.append((p.date, 1, f"Receipt ({p.get_mode_display()}){' - ' + p.note if p.note else ''}",
                        Decimal('0'), p.amount))
    adj = (SalesReturn.objects.filter(bill__customer=customer, refund_mode='ADJUST')
           .select_related('bill').prefetch_related('items'))
    for r in adj:
        entries.append((r.date, 2, f"Credit note {r.note_number} (bill {r.bill.display_number})",
                        Decimal('0'), r.total))
    entries.sort(key=lambda e: (e[0] or date_cls.min, e[1]))
    rows, running = [], Decimal('0')
    for dt, _, text, debit, credit in entries:
        running += debit - credit
        rows.append(dict(date=dt, text=text, debit=debit, credit=credit, balance=running))
    wa_link = (notify.credit_reminder_link(ShopProfile.get().name, customer.name, customer.phone, running)
               if running > 0 else '')
    return page(request, 'app/ledger.html', active='customers', customer=customer, rows=rows, balance=running,
                wa_link=wa_link)


# ======================================================
# SALES RETURN / PURCHASE RETURN (credit note / debit note)
# ======================================================

def _read_return_qtys(request, rows):
    """Reads 'qty_<item pk>' from POST. Returns (qtys: {item: qty}, errors)."""
    qtys, errors = {}, []
    for r in rows:
        raw = request.POST.get(f"qty_{r['item'].pk}", '').strip()
        r['value'] = raw   # so it can be shown back in the form when there is an error
        if not raw:
            continue
        try:
            q = int(raw)
        except ValueError:
            errors.append(f"'{r['name']}': qty must be a number.")
            continue
        if q < 0:
            errors.append(f"'{r['name']}': qty cannot be negative.")
        elif q > r['left']:
            errors.append(f"'{r['name']}': only {r['left']} can be returned.")
        elif q > 0:
            qtys[r['item']] = q
    return qtys, errors


@access.perm_required(('add', 'salesreturn'))
def sales_return_new(request, pk):
    bill = get_object_or_404(Bill.objects.select_related('customer'), pk=pk)
    if bill.status != 'FINAL':
        messages.error(request, 'A return cannot be created for a cancelled bill.')
        return redirect('list', key='bills')
    items = list(bill.items.select_related('product', 'unit').prefetch_related('return_items'))
    rows = []
    for it in items:
        returned = sum(r.qty for r in it.return_items.all())
        rows.append(dict(item=it, name=it.product.name if it.product else '-', unit=it.unit or '',
                         sold=it.qty, returned=returned, left=(it.qty or 0) - returned))

    if request.method == 'POST':
        qtys, errors = _read_return_qtys(request, rows)
        if not errors and not qtys:
            errors.append('Enter a return qty for at least one item.')
        for e in errors:
            messages.error(request, e)
        if not errors:
            try:
                with transaction.atomic():
                    sr = SalesReturn(bill=bill, date=_parse_date(request.POST.get('date')),
                                     refund_mode=request.POST.get('refund_mode', 'CASH'),
                                     reason=request.POST.get('reason', '').strip()[:255])
                    sr.full_clean()
                    sr.save()
                    for it, q in qtys.items():
                        SalesReturnItem(sales_return=sr, invoice_item=it, qty=q).save()
                auditlog.record(request, 'RETURN', 'Sales return', f"{sr.note_number} (bill {bill.display_number})")
                messages.success(request, f"{sr.note_number} created. Stock has been added back ✅")
                return redirect('sales_return_note', pk=sr.pk)
            except ValidationError as e:
                messages.error(request, ' '.join(e.messages))

    return page(request, 'app/return_form.html', active='bills', kind='sales',
                title=f"Sales Return: {bill.display_number}", subtitle=f"{bill.customer_name} · {bill.date:%d %b %Y}",
                rows=rows, today=timezone.localdate().isoformat(), back_url='/bills/',
                can_adjust=bool(bill.customer_id), post=request.POST)


@access.perm_required(('add', 'purchasereturn'))
def purchase_return_new(request, pk):
    purchase = get_object_or_404(Purchase.objects.select_related('supplier'), pk=pk)
    items = list(purchase.items.select_related('product', 'unit', 'batch_record').prefetch_related('return_items'))
    rows = []
    for it in items:
        returned = sum(r.qty for r in it.return_items.all())
        batch = getattr(it, 'batch_record', None)
        in_stock = (batch.qty // (it.unit_factor or 1)) if batch else 0
        rows.append(dict(item=it, name=it.product.name, unit=it.unit or '', sold=it.qty, returned=returned,
                         left=max(min((it.qty or 0) - returned, in_stock), 0),
                         note=f"This batch currently has {batch.qty if batch else 0} (base unit) left"))

    if request.method == 'POST':
        qtys, errors = _read_return_qtys(request, rows)
        if not errors and not qtys:
            errors.append('Enter a return qty for at least one item.')
        for e in errors:
            messages.error(request, e)
        if not errors:
            try:
                with transaction.atomic():
                    pr = PurchaseReturn(purchase=purchase, date=_parse_date(request.POST.get('date')),
                                        reason=request.POST.get('reason', '').strip()[:255])
                    pr.full_clean()
                    pr.save()
                    for it, q in qtys.items():
                        PurchaseReturnItem(purchase_return=pr, purchase_item=it, qty=q).save()
                auditlog.record(request, 'RETURN', 'Purchase return', pr.note_number)
                messages.success(request, f"{pr.note_number} created. Stock and supplier balance adjusted ✅")
                return redirect('purchase_return_note', pk=pr.pk)
            except ValidationError as e:
                messages.error(request, ' '.join(e.messages))

    return page(request, 'app/return_form.html', active='purchases', kind='purchase',
                title=f"Purchase Return: {purchase.invoice_number}", subtitle=f"{purchase.supplier.company} · {purchase.invoice_date:%d %b %Y}",
                rows=rows, today=timezone.localdate().isoformat(), back_url='/purchases/',
                can_adjust=False, post=request.POST)


@access.perm_required(('view', 'salesreturn'))
def sales_return_note(request, pk):
    sr = get_object_or_404(SalesReturn.objects.select_related('bill', 'bill__customer'), pk=pk)
    lines = [dict(n=n, name=i.invoice_item.product.name if i.invoice_item.product else '-',
                  qty=f"{i.qty} {i.invoice_item.unit or ''}".strip(), taxable=i.taxable_value,
                  gst=i.gst, total=i.total)
             for n, i in enumerate(sr.items.select_related('invoice_item__product', 'invoice_item__unit'), 1)]
    return render(request, 'app/note.html', dict(
        kind='credit', title='CREDIT NOTE', number=sr.note_number, date=sr.date, party=sr.bill.customer_name,
        party_gstin=sr.bill.customer.gstin if sr.bill.customer_id else '', ref=f"Bill {sr.bill.display_number}",
        reason=sr.reason, extra=sr.get_refund_mode_display(), lines=lines,
        taxable=sr.taxable, total=sr.total, shop=ShopProfile.get(), back_url='/sales_returns/'))


@access.perm_required(('view', 'purchasereturn'))
def purchase_return_note(request, pk):
    pr = get_object_or_404(PurchaseReturn.objects.select_related('purchase', 'purchase__supplier'), pk=pk)
    lines = [dict(n=n, name=i.purchase_item.product.name, qty=f"{i.qty} {i.purchase_item.unit or ''}".strip(),
                  taxable=i.taxable_value, gst=i.gst, total=i.amount)
             for n, i in enumerate(pr.items.select_related('purchase_item__product', 'purchase_item__unit'), 1)]
    sup = pr.purchase.supplier
    return render(request, 'app/note.html', dict(
        kind='debit', title='DEBIT NOTE', number=pr.note_number, date=pr.date, party=sup.company,
        party_gstin=sup.gstin or '', ref=f"Purchase invoice {pr.purchase.invoice_number}", reason=pr.reason,
        extra='', lines=lines, taxable=pr.taxable, total=pr.total, shop=ShopProfile.get(),
        back_url='/purchase_returns/'))


# ======================================================
# WRITE-OFF (expired batch)
# ======================================================

@access.perm_required(('add', 'stockwriteoff'))
@require_POST
def batch_writeoff(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if batch.qty <= 0:
        messages.error(request, 'This batch has no stock left.')
        return redirect('list', key='batches')
    try:
        StockWriteOff(batch=batch, qty=batch.qty, reason='EXPIRED' if batch.expiry_status == 'Expired' else 'OTHER',
                      note='Full write-off from the Batch list').save()
        auditlog.record(request, 'WRITEOFF', 'Batch', f"{batch.batch_number} · qty {batch.qty}")
        messages.success(request, f"{batch.batch_number} fully written off (it will be counted as a loss in the P&L).")
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('list', key='batches')


# ======================================================
# GST REPORT + DRUG REGISTER
# ======================================================

@staff_required
def gst_view(request):
    today, month, year = _month_year(request)
    rep = reports.gst_report(month, year)
    if request.GET.get('export') == 'xlsx':
        return _xlsx(reports.gst_workbook(rep), f"gst-report-{year}-{month:02d}.xlsx")
    return page(request, 'app/gst.html', active='gst', rep=rep, **_month_ctx(today, month, year))


def _fy_start(day):
    return date_cls(day.year if day.month >= 4 else day.year - 1, 4, 1)


@staff_required
def ca_export_view(request):
    """Excel for the accountant: sales / purchase registers, credit / debit notes, expenses and payments of a date range."""
    today = timezone.localdate()
    first = today.replace(day=1)
    fy = _fy_start(today)
    last_month_end = first - timedelta(days=1)
    presets = [('This month', first, today), ('Last month', last_month_end.replace(day=1), last_month_end),
               ('This financial year', fy, today), ('Last financial year', date_cls(fy.year - 1, 4, 1), fy - timedelta(days=1))]
    start, end = _parse_date(request.GET.get('from'), first), _parse_date(request.GET.get('to'), today)
    error = ''
    try:
        ca_export.check_range(start, end)
    except ca_export.RangeError as e:
        error = str(e)
    if request.GET.get('export') == 'xlsx' and not error:
        auditlog.record(request, 'EXPORT', 'CA export', f'{start} to {end}')
        return _xlsx(ca_export.build_workbook(start, end), f"medsync-accounts-{start:%Y%m%d}-{end:%Y%m%d}.xlsx")
    info = None if error else ca_export.summary(start, end)
    return page(request, 'app/ca_export.html', active='ca_export', start=start, end=end, error=error, info=info,
                presets=[dict(label=l, start=a, end=b) for l, a, b in presets])


@staff_required
def health_view(request):
    """The health score: what was measured, and the reason for every point lost. POST = measure again now."""
    if request.method == 'POST':
        try:
            health.refresh()
            messages.success(request, 'The health score was measured again.')
        except Exception:
            messages.error(request, 'Could not measure the health score right now. Please try again.')
        return redirect('health')
    try:
        data, problem = health.current(max_age_minutes=60), ''
    except Exception:
        data, problem = None, 'The health score could not be measured right now. Please try again.'
    checked = None
    if data:
        try:
            checked = timezone.localtime(datetime_cls.fromisoformat(data['checked_at']))
        except (KeyError, ValueError, TypeError):
            checked = None
    return page(request, 'app/health.html', active='health', data=data, problem=problem, checked=checked,
                history=health.history() if data else [])


SCHEDULE_FILTERS = {'H1X': ('H1', 'X'), 'H': ('H',), 'ALL': ('H', 'H1', 'X')}


@access.perm_required(('view', 'bill'))
def drug_register_view(request):
    today, month, year = _month_year(request)
    sel = request.GET.get('schedule', 'H1X')
    if sel not in SCHEDULE_FILTERS:
        sel = 'H1X'
    rows = reports.drug_register(month, year, SCHEDULE_FILTERS[sel])
    if request.GET.get('export') == 'xlsx':
        return _xlsx(reports.register_workbook(rows), f"drug-register-{year}-{month:02d}.xlsx")
    return page(request, 'app/drug_register.html', active='drug_register', rows=rows, sel=sel,
                **_month_ctx(today, month, year))


# ======================================================
# BARCODE / QUICK PRODUCT LOOKUP (billing screen)
# ======================================================

def _bill_rate(p):
    """Billing rate (before GST) of 1 base unit, worked out from the MRP (which includes GST). The bill adds GST on
    top, so the customer pays exactly the MRP. '' when the product has no MRP."""
    if p.mrp is None:
        return ''
    return str((Decimal(p.mrp) / (1 + Decimal(p.gst_rate or 0) / 100)).quantize(Decimal('0.0001')))


def _unit_factors(p):
    """{unit id: how many base units} for the base unit and every pack unit of the product."""
    factors = {str(c.unit_id): c.factor for c in p.unit_conversions.all()}
    if p.unit_id:
        factors[str(p.unit_id)] = 1
    return factors


@access.perm_required(('add', 'bill'), ('change', 'bill'))
def customer_lookup(request):
    """Billing helper: find a customer by phone number (last 10 digits) or id; returns name and credit due."""
    pid = request.GET.get('id', '').strip()
    customer = None
    if pid.isdigit():
        customer = Customer.objects.filter(pk=int(pid), is_active=True).first()
    else:
        digits = re.sub(r'\D', '', request.GET.get('phone', ''))
        if len(digits) >= 10:
            tail = digits[-10:]
            for cid, ph in Customer.objects.filter(is_active=True, phone__isnull=False).values_list('id', 'phone'):
                if re.sub(r'\D', '', ph)[-10:] == tail:
                    customer = Customer.objects.get(pk=cid)
                    break
    if not customer:
        return JsonResponse({'found': False})
    balance, limit = customer.balance(), customer.credit_limit or Decimal('0')
    return JsonResponse({'found': True, 'id': customer.pk, 'name': customer.name, 'phone': customer.phone or '',
                         'balance': str(balance.quantize(Decimal('0.01'))), 'limit': str(limit),
                         'over': bool(limit > 0 and balance >= limit)})


# ======================================================
# SALARY SHEET (who was paid how much in a month)
# ======================================================

@access.perm_required(('view', 'employee'))
def salary_view(request):
    post_recurring(request)
    today, month, year = _month_year(request)
    first, last = date_cls(year, month, 1), date_cls(year, month, calendar.monthrange(year, month)[1])
    paid = {r['employee_id']: r['s'] for r in Expense.objects.filter(
        name='SALARY', employee__isnull=False, date__year=year, date__month=month
    ).values('employee_id').annotate(s=Sum('amount'))}
    emps = Employee.objects.filter(
        Q(pk__in=list(paid)) | (Q(joining_date__lte=last) & (Q(leaving_date__isnull=True) | Q(leaving_date__gte=first)))
    ).order_by('name')
    rows, tot_salary, tot_paid, tot_pending = [], Decimal('0'), Decimal('0'), Decimal('0')
    for e in emps:
        got, salary = paid.get(e.pk, Decimal('0')), e.monthly_salary
        pending = max(salary - got, Decimal('0'))
        due = date_cls(year, month, min(e.pay_day, 28))
        if not salary and not got:
            status, cls = 'No salary set', 'grey'
        elif got >= salary and got > 0:
            status, cls = 'Paid', 'green'
        elif got > 0:
            status, cls = 'Part paid', 'amber'
        elif due > today:
            status, cls = f"Due on {due:%d %b}", 'grey'
        else:
            status, cls = 'Not paid', 'red'
        if got > 0 or due <= today:          # a salary whose pay day has not come yet is not "pending" yet
            tot_pending += pending
        tot_salary += salary
        tot_paid += got
        rows.append(dict(emp=e, salary=salary, paid=got, pending=pending, status=status, cls=cls))
    fmt = request.GET.get('export')
    if fmt in ('xlsx', 'csv'):
        return table_download(f"medsync_salary_{year}{month:02d}",
                              ['Employee ID', 'Name', 'Designation', 'Salary', 'Paid', 'Pending', 'Status'],
                              [[r['emp'].code, r['emp'].name, r['emp'].designation, r['salary'], r['paid'], r['pending'],
                                r['status']] for r in rows], fmt)
    return page(request, 'app/salary.html', active='salary', rows=rows, tot_salary=tot_salary, tot_paid=tot_paid,
                tot_pending=tot_pending, can_add_expense=access.can(request.user, 'add', 'expense'),
                **_month_ctx(today, month, year))


# ======================================================
# QUICK ADD PRODUCT (from the purchase form)
# ======================================================

@access.perm_required(('add', 'product'))
def product_suggest(request):
    """Defaults for a new product: copied from a similar product (same first word of the name),
    otherwise from the most recently added product."""
    name = request.GET.get('name', '').strip()
    base = Product.objects.select_related('category', 'brand', 'unit').prefetch_related('unit_conversions__unit')
    first = name.split()[0] if name else ''
    similar = base.filter(name__istartswith=first).order_by('-id').first() if len(first) >= 3 else None
    p = similar or base.order_by('-id').first()
    if not p:
        return JsonResponse({'found': False})
    packs = [c for c in p.unit_conversions.all() if c.factor > 1]
    pack = max(packs, key=lambda c: c.factor) if packs else None
    return JsonResponse({
        'found': True, 'similar': bool(similar), 'from': p.name or str(p),
        'category': p.category.name if p.category_id else '', 'brand': p.brand.name if p.brand_id else '',
        'gst': str(int(p.gst_rate)), 'unit': str(p.unit_id or ''), 'schedule': p.schedule,
        'pack_unit': str(pack.unit_id) if pack else '', 'pack_factor': pack.factor if pack else ''})


@access.perm_required(('add', 'purchase'), ('change', 'purchase'))
def product_find(request):
    """Purchase screen: type a medicine name / barcode / SKU and get the matching products (max 8)."""
    q = ' '.join(request.GET.get('q', '').split())
    if len(q) < 2:
        return JsonResponse({'results': []})
    qs = (Product.objects.exclude(is_active=False)
          .filter(Q(name__icontains=q) | Q(composition__icontains=q) | Q(barcode=q) | Q(sku__iexact=q))
          .select_related('unit').order_by('name')[:8])
    low = q.lower()
    results = [dict(id=p.pk, label=str(p), composition=p.composition or '', unit=p.unit.name if p.unit_id else '',
                    exact=(p.name or '').strip().lower() == low or (p.barcode or '') == q or (p.sku or '').lower() == low)
               for p in qs]
    return JsonResponse({'results': results})


@access.perm_required(('add', 'product'))
@require_POST
def product_quick_add(request):
    """Creates a product (and its pack conversion) from the small form on the purchase screen."""
    def fail(msg, **extra):
        return JsonResponse(dict(ok=False, error=msg, **extra), status=400)

    post = request.POST
    name = ' '.join(post.get('name', '').split())
    if not name:
        return fail('Product name is required.')
    if len(name) > 255:
        return fail('Product name is too long.')
    twin = Product.objects.filter(name__iexact=name).first()
    if twin:
        return fail(f"'{twin.name}' already exists. Please use that product.", existing=dict(id=twin.pk, label=str(twin)))

    try:
        gst = Decimal(post.get('gst_rate', ''))
    except Exception:
        return fail('Please choose the GST rate.')
    if gst not in GST_ALLOWED:
        return fail('GST rate must be 0, 5, 12 or 18.')
    unit = Unit.objects.filter(pk=post.get('unit', '')).first() if post.get('unit', '').isdigit() else None
    if not unit:
        return fail('Please choose the base unit (the smallest unit, e.g. Tablet).')

    pack_unit, pack_factor = None, None
    raw_pu, raw_pf = post.get('pack_unit', ''), post.get('pack_factor', '').strip()
    if raw_pu or raw_pf:
        pack_unit = Unit.objects.filter(pk=raw_pu).first() if raw_pu.isdigit() else None
        if not pack_unit or not raw_pf.isdigit() or int(raw_pf) < 2:
            return fail('For a pack, choose the pack unit and enter how many base units it holds (2 or more).')
        if pack_unit.pk == unit.pk:
            return fail('The pack unit must be different from the base unit.')
        pack_factor = int(raw_pf)

    schedule = post.get('schedule', 'NONE') or 'NONE'
    if schedule not in dict(Product.SCHEDULE_CHOICES):
        return fail('Invalid drug schedule.')
    barcode = post.get('barcode', '').strip() or None
    if barcode:
        if len(barcode) > 64:
            return fail('Barcode is too long.')
        other = Product.objects.filter(barcode=barcode).first()
        if other:
            return fail(f"This barcode already belongs to '{other.name}'.", existing=dict(id=other.pk, label=str(other)))

    try:
        with transaction.atomic():
            product = Product(
                name=name, composition=post.get('composition', '').strip()[:255] or None, gst_rate=gst, unit=unit,
                schedule=schedule, barcode=barcode,
                category=_fk(Category, post.get('category', '')[:255]), brand=_fk(Brand, post.get('brand', '')[:255]))
            product.full_clean()
            product.save()
            if pack_unit:
                ProductUnit.objects.create(product=product, unit=pack_unit, factor=pack_factor)
    except ValidationError as e:
        return fail(' '.join(e.messages))
    except IntegrityError:
        return fail('This product (or its barcode) already exists.')
    auditlog.record(request, 'CREATE', 'Product', f"{product.name} (added from a purchase entry)")
    return JsonResponse({'ok': True, 'id': product.pk, 'label': str(product)})


@access.perm_required(('add', 'purchase'), ('change', 'purchase'))
def last_purchase(request):
    """Items of a supplier's latest invoice (product, qty, unit; no prices) so a repeat order can be copied."""
    try:
        sid = int(request.GET.get('supplier', ''))
    except (TypeError, ValueError):
        return JsonResponse({'found': False})
    p = Purchase.objects.filter(supplier_id=sid).order_by('-invoice_date', '-id').first()
    if not p:
        return JsonResponse({'found': False})
    items = [dict(product=str(i.product_id), name=i.product.name or str(i.product), qty=i.qty,
                  unit=str(i.unit_id) if i.unit_id else '')
             for i in p.items.select_related('product').order_by('id') if i.product.is_active is not False]
    return JsonResponse({'found': bool(items), 'invoice': p.invoice_number,
                         'date': p.invoice_date.strftime('%d %b %Y'), 'items': items})


def _batch_info(product_ids):
    """Billing helper. Per product: the sellable batches in the order they will be sold (FEFO: earliest expiry first,
    no date last, expired ones skipped), and what is stuck in expired batches. Quantities are in base units."""
    today = timezone.localdate()
    out = {pid: {'batches': [], 'expired_qty': 0, 'expired_batches': 0} for pid in product_ids}
    rows = Batch.objects.filter(product_id__in=product_ids, qty__gt=0).order_by(F('expire_date').asc(nulls_last=True), 'id')
    for b in rows:
        o = out[b.product_id]
        if b.expire_date and b.expire_date < today:
            o['expired_qty'] += b.qty
            o['expired_batches'] += 1
        elif len(o['batches']) < 6:
            where = ' · '.join(x for x in (f'Rack {b.rack}' if b.rack else '', f'Row {b.row}' if b.row else '', b.location or '') if x)
            o['batches'].append(dict(id=b.pk, no=b.batch_number or 'No batch no.', qty=b.qty, where=where,
                                     exp=b.expire_date.isoformat() if b.expire_date else '',
                                     days=(b.expire_date - today).days if b.expire_date else None))
    return out


@access.perm_required(('add', 'bill'), ('change', 'bill'))
def product_lookup(request):
    q = request.GET.get('q', '').strip()
    pid = request.GET.get('id', '').strip()
    base = Product.objects.exclude(is_active=False)
    if pid.isdigit():
        qs = base.filter(pk=int(pid))                      # exact product (used when a product is picked from the list)
    elif q:
        qs = base.filter(Q(barcode=q) | Q(sku__iexact=q) | Q(name__icontains=q) | Q(composition__icontains=q))
    else:
        return JsonResponse({'results': [], 'exact': False})
    qs = qs.select_related('unit').prefetch_related('unit_conversions__unit', 'batches').order_by('name')[:8]
    products = list(qs)
    batch_info = _batch_info([p.pk for p in products]) if pid.isdigit() else {}     # only when one product is picked
    results = [dict(id=p.pk, name=p.name or str(p), composition=p.composition or '', barcode=p.barcode or '',
                    mrp=str(p.mrp) if p.mrp is not None else '', rate1=_bill_rate(p), factors=_unit_factors(p),
                    stock=p.stock_display(), stock_qty=p.stock_qty, available_qty=p.available_qty(), schedule=p.schedule,
                    unit_name=p.unit.name if p.unit_id else '', **batch_info.get(p.pk, {})) for p in products]
    if pid.isdigit() and results:       # a picked product: the same-salt medicines that can be sold instead (shown when it is out / short)
        results[0]['alts'] = [dict(id=a.pk, name=a.name or str(a), stock=a.stock_display(a.available_qty()), available_qty=a.available_qty(),
                                   mrp=str(a.mrp) if a.mrp is not None else '', unit_name=a.unit.name if a.unit_id else '',
                                   schedule=a.schedule) for a in products[0].get_alternatives(limit=5)]
    exact = any(r['barcode'] == q for r in results)
    return JsonResponse({'results': results, 'exact': exact})


# ======================================================
# REORDER SUGGESTIONS (from sales speed)
# ======================================================

def _choice(raw, allowed, default):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return v if v in allowed else default


def _supplier_for(product_ids, defaults, today):
    """product id -> supplier id: the supplier whose latest price is the lowest, else the product's default supplier,
    else the supplier of the last purchase (same rule as the automatic purchase order)."""
    by_product = {}
    for r in supplier_prices.rows_from_items(PurchaseItem.objects.filter(product_id__in=product_ids)):
        by_product.setdefault(r['product_id'], []).append(r)
    out = {}
    for pid in product_ids:
        sid = supplier_prices.pick_supplier(by_product.get(pid, []), defaults.get(pid), today)
        out[pid] = sid
    return out


def _reorder_suggestions(days, cover, today):
    since = today - timedelta(days=days)
    sold = {r['product_id']: r['s'] for r in InvoiceItem.objects.filter(
        bill__status='FINAL', bill__date__gte=since, product__isnull=False).values('product_id').annotate(s=Sum('base_qty'))}
    expired = {r['product_id']: r['s'] for r in Batch.objects.filter(
        qty__gt=0, expire_date__lt=today, product__isnull=False).values('product_id').annotate(s=Sum('qty'))}
    on_order = {r['product_id']: r['s'] for r in PurchaseOrderItem.objects.filter(
        purchase_order__status__in=['PENDING', 'SENT']).values('product_id').annotate(s=Sum('order_qty'))}
    products, defaults = [], {}
    for p in Product.objects.exclude(is_active=False).select_related('unit').prefetch_related('unit_conversions__unit'):
        packs = [c for c in p.unit_conversions.all() if c.factor > 1]
        pack = max(packs, key=lambda c: c.factor) if packs else None
        products.append(dict(id=p.pk, name=p.name or str(p), unit=p.unit.name if p.unit_id else '',
                             stock_qty=p.stock_qty, reorder_level=p.reorder_level,
                             pack_name=pack.unit.name if pack else '', pack_factor=pack.factor if pack else 0))
        defaults[p.pk] = p.default_supplier_id
    rows = reorder.suggest(products, sold, expired, on_order, days=days, cover=cover)
    sup = _supplier_for([r['id'] for r in rows], defaults, today)
    names = dict(Supplier.objects.filter(pk__in={s for s in sup.values() if s}).values_list('pk', 'company'))
    for r in rows:
        r['supplier_id'] = sup.get(r['id'])
        r['supplier'] = names.get(r['supplier_id'], '')
    return rows


@access.perm_required(('view', 'purchaseorder'))
def reorder_view(request):
    today = timezone.localdate()
    levels.refresh_daily(today)
    if request.method == 'POST':
        if not access.can(request.user, 'add', 'purchaseorder'):
            raise PermissionDenied
        picked = {}
        for raw in request.POST.getlist('pick'):
            if raw.isdigit():
                try:
                    qty = int(request.POST.get(f'qty_{raw}', '0') or 0)
                except ValueError:
                    qty = 0
                if 0 < qty <= 100_000_000:
                    picked[int(raw)] = qty
        if not picked:
            messages.warning(request, 'Select at least one product with a quantity.')
            return redirect('reorder')
        products = {p.pk: p for p in Product.objects.filter(pk__in=picked).exclude(is_active=False)}
        busy = set(PurchaseOrderItem.objects.filter(
            product_id__in=picked, purchase_order__status__in=['PENDING', 'SENT']).values_list('product_id', flat=True))
        sup = _supplier_for(list(products), {pk: p.default_supplier_id for pk, p in products.items()}, today)
        made, no_supplier, skipped, pos = 0, [], [], {}
        with transaction.atomic():
            for pk, qty in picked.items():
                p = products.get(pk)
                if not p:
                    continue
                if pk in busy:
                    skipped.append(p.name or str(p))
                    continue
                sid = sup.get(pk)
                if not sid:
                    no_supplier.append(p.name or str(p))
                    continue
                if sid not in pos:
                    pos[sid] = (PurchaseOrder.objects.filter(supplier_id=sid, status='PENDING').first()
                                or PurchaseOrder.objects.create(supplier_id=sid))
                PurchaseOrderItem.objects.create(purchase_order=pos[sid], product=p, order_qty=qty)
                made += 1
        if made:
            numbers = ', '.join(po.po_number for po in pos.values())
            auditlog.record(request, 'CREATE', 'Purchase Order', f"{numbers} (reorder suggestions, {made} item(s))")
            messages.success(request, f"{made} product(s) added to purchase order {numbers}. Review and send it from here.")
        if skipped:
            messages.info(request, 'Already on a pending purchase order: ' + ', '.join(skipped) + '.')
        if no_supplier:
            messages.warning(request, 'No supplier known yet (set a Default Supplier on the product): ' + ', '.join(no_supplier) + '.')
        return redirect('list', key='purchase_orders') if made else redirect('reorder')

    days = _choice(request.GET.get('days'), (15, 30, 60, 90), 30)
    cover = _choice(request.GET.get('cover'), (15, 30, 45, 60), 30)
    rows = _reorder_suggestions(days, cover, today)
    return page(request, 'app/reorder.html', active='reorder', rows=rows, days=days, cover=cover,
                days_opts=(15, 30, 60, 90), cover_opts=(15, 30, 45, 60), urgent=sum(1 for r in rows if r['urgent']))


# ======================================================
# ALERTS + PURCHASE ORDER SHARE
# ======================================================

@staff_required
@require_POST
def alerts_send(request):
    try:
        sent, recipients = notify.send_alert_digest()
    except Exception as exc:   # SMTP/network error: show the user a clear message
        messages.error(request, f"Email could not be sent: {exc}")
        return redirect('dashboard')
    if not recipients:
        messages.warning(request, 'No email address found. Add an email in Shop Settings or set DJANGO_ALERT_EMAILS.')
    else:
        messages.success(request, f"Alert digest sent: {', '.join(recipients)}")
    return redirect('dashboard')


@access.perm_required(('view', 'bill'))
def refill_view(request):
    """Regular customers whose medicine is about to finish (from their own buying pattern), with a WhatsApp button each."""
    today = timezone.localdate()
    since = today - timedelta(days=300)
    rows = [dict(customer_id=c, product_id=p, date=d) for c, p, d in InvoiceItem.objects.filter(
        bill__status='FINAL', bill__customer__isnull=False, bill__customer__is_active=True,
        product__isnull=False, bill__date__gte=since).values_list('bill__customer_id', 'product_id', 'bill__date')]
    due = refill.due_refills(rows, today)
    customers = Customer.objects.in_bulk([c['customer_id'] for c in due])
    products = Product.objects.in_bulk(list({i['product_id'] for c in due for i in c['items']}))
    shop = ShopProfile.get()
    out = []
    for c in due:
        cust = customers.get(c['customer_id'])
        items = [dict(name=products[i['product_id']].name or str(products[i['product_id']]), last=i['last'],
                      expected=i['expected'], late=i['late']) for i in c['items'] if i['product_id'] in products
                 and products[i['product_id']].is_active is not False]
        if not cust or not items:
            continue
        digits = ''.join(ch for ch in (cust.phone or '') if ch.isdigit())
        link = notify.refill_reminder_link(shop.name, cust.name, cust.phone, [i['name'] for i in items]) if len(digits) >= 10 else ''
        late = c['late']
        out.append(dict(customer=cust, items=items, link=link, late=late,
                        status='Due in %d day%s' % (-late, '' if late == -1 else 's') if late < 0
                        else 'Due today' if late == 0 else 'Late by %d day%s' % (late, '' if late == 1 else 's'),
                        cls='grey' if late <= 0 else 'amber' if late <= 7 else 'red'))
    return page(request, 'app/refill.html', active='refill', rows=out, no_phone=sum(1 for r in out if not r['link']),
                grace=refill.GRACE)


def _expiry_batches(qs):
    return qs.select_related('product', 'product__unit', 'purchase_item', 'purchase_item__unit', 'purchase_item__purchase',
                             'purchase_item__purchase__supplier')


def _expiry_rows(batches):
    rows = []
    for b in batches:
        pi = b.purchase_item
        pu = pi.purchase
        rows.append(dict(
            batch_id=b.pk, product=b.product.name or str(b.product), batch_number=b.batch_number, expire_date=b.expire_date,
            qty=b.qty, factor=pi.unit_factor or 1,
            unit=pi.unit.name if pi.unit_id else (b.product.unit.name if b.product.unit_id else ''),
            supplier_id=pu.supplier_id, supplier=pu.supplier.company, phone=pu.supplier.phone or '',
            purchase_id=pu.pk, invoice=pu.invoice_number, invoice_date=pu.invoice_date,
            total=pi.purchase_price or 0, line_qty=pi.qty))
    return rows


def _expiry_make_returns(request, today):
    """Books a purchase return (debit note) per original invoice for the ticked batches, all or nothing."""
    ids = [int(x) for x in request.POST.getlist('batch') if x.isdigit()]
    if not ids:
        messages.error(request, 'Tick at least one batch to return.')
        return redirect('expiry')
    reason = ' '.join(request.POST.get('reason', '').split())[:255] or 'Expired / near expiry'
    batches = _expiry_batches(Batch.objects.filter(pk__in=ids, qty__gt=0, purchase_item__isnull=False, product__isnull=False))
    by_purchase = {}
    for b in batches:
        pi = b.purchase_item
        units, _loose = expiry_returns.returnable_units(b.qty, pi.unit_factor)
        if units > 0:
            by_purchase.setdefault(pi.purchase_id, (pi.purchase, []))[1].append((pi, units))
    if not by_purchase:
        messages.error(request, 'Nothing could be returned. These batches may already be sold, returned, or less than one full unit.')
        return redirect('expiry')
    made = []
    try:
        with transaction.atomic():
            for purchase, lines in by_purchase.values():
                ret = PurchaseReturn.objects.create(purchase=purchase, date=today, reason=reason)
                for pi, units in lines:
                    PurchaseReturnItem(purchase_return=ret, purchase_item=pi, qty=units).save()
                made.append(ret)
    except ValidationError as e:
        messages.error(request, 'Nothing was returned: ' + ' '.join(e.messages))
        return redirect('expiry')
    total = sum((r.total for r in made), Decimal('0'))
    for r in made:
        auditlog.record(request, 'CREATE', 'Purchase Return', f"{r} (expiry return)")
    messages.success(request, f"{len(made)} debit note(s) created, worth \u20b9{total:,.2f}. Stock is reduced and the supplier balance is updated.")
    return redirect('list', key='purchase_returns')


@access.perm_required(('add', 'purchasereturn'))
def expiry_view(request):
    """Expired / soon-to-expire batches, by supplier, with a tick-and-return button (debit note) and a WhatsApp message."""
    today = timezone.localdate()
    if request.method == 'POST':
        return _expiry_make_returns(request, today)
    try:
        days = int(request.GET.get('days', expiry_returns.DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = expiry_returns.DEFAULT_DAYS
    days = days if days in expiry_returns.DAY_CHOICES else expiry_returns.DEFAULT_DAYS
    batches = _expiry_batches(Batch.objects.filter(
        qty__gt=0, expire_date__isnull=False, expire_date__lte=today + timedelta(days=days),
        purchase_item__isnull=False, product__isnull=False))
    groups = expiry_returns.plan(_expiry_rows(batches), today, days)
    shop = ShopProfile.get()
    for g in groups:
        items = [x for inv in g['invoices'] for x in inv['items'] if x['returnable']]
        g['wa'] = notify.whatsapp_link(expiry_returns.whatsapp_text(shop.name, g['supplier'], items), g['phone']) if items else ''
    return page(request, 'app/expiry.html', active='expiry', groups=groups, days=days, day_choices=expiry_returns.DAY_CHOICES,
                grand=sum((g['total'] for g in groups), Decimal('0')), today=today)


@access.perm_required(('view', 'product'))
def product_preview(request, pk):
    """The same data as the Preview window of the Products list (which normally has it embedded already)."""
    p = get_object_or_404(Product.objects.select_related('unit', 'brand', 'category', 'default_supplier')
                          .prefetch_related('unit_conversions__unit', models.Prefetch('batches', queryset=Batch.objects.select_related('supplier'))), pk=pk)
    return JsonResponse(billview.product_preview_data(
        p, timezone.localdate(),
        edit_url=reverse('edit', args=['products', p.pk]) if access.can(request.user, 'change', 'product') else '',
        prices_url=f'/supplier_prices/{p.pk}/'))


HOLD_LIMIT = 30                  # most bills that can be on hold at the same time
HOLD_DROP = {'csrfmiddlewaretoken', 'held_id', 'confirm_warnings'}
ITEM_PRODUCT = re.compile(r'^[\w]+-\d+-product$')


@access.perm_required(('add', 'bill'))
@require_POST
def bill_hold(request):
    """Parks the bill that is being typed (the fields of the new-bill form). Stock and numbers are not touched."""
    def fail(msg):
        return JsonResponse(dict(ok=False, error=msg), status=400)

    try:
        fields = json.loads(request.body.decode('utf-8') or '{}').get('fields')
    except (ValueError, AttributeError):
        return fail('Could not read the bill.')
    if (not isinstance(fields, list) or len(fields) > 3000
            or not all(isinstance(f, list) and len(f) == 2 and all(isinstance(x, str) for x in f) and len(f[0]) <= 120 and len(f[1]) <= 2000
                       for f in fields)):
        return fail('Could not read the bill.')
    fields = [f for f in fields if f[0] not in HOLD_DROP]
    count = sum(1 for n, v in fields if ITEM_PRODUCT.match(n) and v.strip())
    if not count:
        return fail('Add at least one medicine before putting the bill on hold.')
    if HeldBill.objects.count() >= HOLD_LIMIT:
        return fail(f'{HOLD_LIMIT} bills are already on hold. Finish or discard some first.')
    name = next((v.strip() for n, v in fields if n == 'customer_name'), '')
    held = HeldBill.objects.create(user=request.user.get_username(), label=(name or 'Walk-in')[:150], items_count=count, payload=fields)
    auditlog.record(request, 'HOLD', 'Bill', held.label)
    messages.success(request, f'Bill of {held.label} is on hold ({count} item{"" if count == 1 else "s"}). Open it again from the list above.')
    return JsonResponse(dict(ok=True, id=held.pk, count=count, label=held.label))


@access.perm_required(('add', 'bill'))
@require_POST
def bill_hold_discard(request, pk):
    held = HeldBill.objects.filter(pk=pk).first()
    if held:
        held.delete()
        messages.success(request, 'The held bill was discarded.')
    return redirect('new', key='bills')


@access.perm_required(('view', 'purchase'))
def purchase_preview(request, pk):
    """The same data as the Preview window of the Purchases list (which normally has it embedded already)."""
    p = get_object_or_404(Purchase.objects.select_related('supplier'), pk=pk)
    items = list(p.items.select_related('product', 'unit'))
    return JsonResponse(billview.purchase_preview_data(
        p, items, timezone.localdate(),
        edit_url=reverse('edit', args=['purchases', p.pk]) if access.can(request.user, 'change', 'purchase') else '',
        return_url=reverse('purchase_return_new', args=[p.pk]) if access.can(request.user, 'add', 'purchasereturn') else ''))


@access.perm_required(('view', 'bill'))
def bill_preview(request, pk):
    """The same data as the Preview window of the Billing list (which normally has it embedded already)."""
    bill = get_object_or_404(Bill.objects.select_related('customer'), pk=pk)
    items = list(bill.items.select_related('product', 'unit'))
    batch_map = Batch.objects.in_bulk({int(k) for it in items for k in (it.batch_history or {})})
    return JsonResponse(billview.preview_data(bill, items, batch_map, reverse('invoice', args=[bill.pk])))


@access.perm_required(('view', 'product'), ('view', 'batch'), ('view', 'sale'))
def alerts_whatsapp(request):
    """Opens WhatsApp with the short stock-alert message. It goes to the 'Alerts WhatsApp number' of Shop Settings
    (manager / staff), else the shop phone; with no number WhatsApp lets the user pick a contact."""
    shop = ShopProfile.get()
    return redirect(notify.whatsapp_link(notify.alerts_whatsapp_text(shop.name), shop.alert_phone or shop.phone or ''))


@access.perm_required(('view', 'purchaseorder'))
def po_print(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk)
    items = po.items.select_related('product', 'product__unit')
    return render(request, 'app/po_print.html', dict(po=po, items=items, shop=ShopProfile.get()))


@access.perm_required(('change', 'purchaseorder'))
@require_POST
def po_email(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk)
    if not po.supplier.email:
        messages.error(request, f"{po.supplier.company} does not have an email address in Suppliers.")
        return redirect('list', key='purchase_orders')
    shop = ShopProfile.get()
    from django.core.mail import send_mail
    from django.conf import settings
    try:
        send_mail(f"Purchase Order {po.po_number} - {shop.name}", notify.po_text(po, shop),
                  settings.DEFAULT_FROM_EMAIL, [po.supplier.email], fail_silently=False)
    except Exception as exc:
        messages.error(request, f"Email could not be sent: {exc}")
        return redirect('list', key='purchase_orders')
    if po.status == 'PENDING':
        po.status = 'SENT'
        po.save(update_fields=['status'])
    messages.success(request, f"{po.po_number} sent to {po.supplier.email} ✅")
    return redirect('list', key='purchase_orders')


@access.perm_required(('change', 'purchaseorder'))
def po_whatsapp(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk)
    return redirect(notify.whatsapp_link(notify.po_text(po, ShopProfile.get()), po.supplier.phone or ''))


@staff_required
def summary_whatsapp(request):
    """Opens WhatsApp with today's summary, addressed to the shop's own number (a message to yourself)."""
    shop = ShopProfile.get()
    return redirect(notify.whatsapp_link(summary.current_text(timezone.localdate()), shop.phone or ''))


# ======================================================
# PHYSICAL STOCK AUDIT
# ======================================================

@staff_required
@require_POST
def audit_start(request):
    audit = StockAudit.start(note=request.POST.get('note', '').strip()[:255])
    messages.success(request, f"Stock Audit #{audit.pk} started. Now enter the counted quantity of each product.")
    return redirect('audit_count', pk=audit.pk)


@staff_required
def audit_count(request, pk):
    audit = get_object_or_404(StockAudit, pk=pk)
    items = list(audit.items.select_related('product', 'product__unit').order_by('product__name'))
    q = request.GET.get('q', '').strip().lower()
    editable = audit.status == 'OPEN'

    if request.method == 'POST' and editable:
        changed, errors = [], []
        for it in items:
            raw = request.POST.get(f"count_{it.pk}", '').strip()
            if raw == '':
                new = None
            else:
                try:
                    new = int(raw)
                    if new < 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"'{it.product.name}': the count must be a whole number, 0 or more.")
                    continue
            if new != it.counted_qty:
                it.counted_qty = new
                changed.append(it)
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            StockAuditItem.objects.bulk_update(changed, ['counted_qty'])
            if request.POST.get('action') == 'apply':
                try:
                    audit.apply()
                    auditlog.record(request, 'STOCK-AUDIT', 'Stock Audit', f"#{audit.pk} applied; stock set to counted quantities")
                    messages.success(request, 'Stock adjusted ✅ (products with a counted quantity now match their count).')
                except ValidationError as e:
                    messages.error(request, ' '.join(e.messages))
            else:
                messages.success(request, f"Count saved for {len(changed)} product(s).")
            return redirect('audit_count', pk=audit.pk)

    shown = [it for it in items if not q or q in (it.product.name or '').lower()]
    counted = sum(1 for it in items if it.counted_qty is not None)
    return page(request, 'app/stock_count.html', active='stock_audits', audit=audit, items=shown,
                editable=editable, q=request.GET.get('q', ''), counted=counted, total=len(items))


# ======================================================
# DATABASE BACKUP (staff only)
# ======================================================

@staff_required
def backup_page(request):
    if request.method == 'POST':
        try:
            path = backup.make_backup()
        except backup.BackupError as e:
            messages.error(request, str(e))
            return redirect('backup')
        auditlog.record(request, 'BACKUP', 'Database', path.name)
        if request.POST.get('action') == 'download':
            return redirect('backup_download', name=path.name)
        messages.success(request, f"Backup created: {path.name}")
        return redirect('backup')
    return page(request, 'app/backup.html', active='backup', backups=backup.list_backups(),
                folder=str(backup.backup_dir()))


@staff_required
def backup_download(request, name):
    path = backup.backup_path(name)
    if not path:
        raise Http404
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=path.name)


# ======================================================
# SUPPLIER PRICE COMPARISON (read-only, built from purchase entries)
# ======================================================

def _price_rows(qs):
    """PurchaseItem queryset -> plain dicts for supplier_prices."""
    return supplier_prices.rows_from_items(qs)


@access.perm_required(('view', 'purchase'))
def supplier_prices_view(request):
    """All products with the cheapest and costliest supplier price."""
    q = request.GET.get('q', '').strip()
    multi = request.GET.get('multi') == '1'
    qs = PurchaseItem.objects.all()
    if q:
        qs = qs.filter(Q(product__name__icontains=q) | Q(product__composition__icontains=q)
                       | Q(product__barcode=q) | Q(product__sku__iexact=q))
    rows = supplier_prices.overview(_price_rows(qs), timezone.localdate())
    if multi:
        rows = [r for r in rows if r['suppliers_n'] > 1]
    fmt = request.GET.get('export')
    if fmt in ('xlsx', 'csv'):
        data = [[r['product'], r['base_unit'], r['suppliers_n'], r['best_supplier'], r['best_rate'],
                 r['worst_supplier'], r['worst_rate'], r['saving_pct'] if r['saving_pct'] is not None else '',
                 r['last_date']] for r in rows]
        heads = ['Product', 'Base unit', 'Suppliers', 'Cheapest supplier', 'Cheapest price (per base unit)',
                 'Costliest supplier', 'Costliest price (per base unit)', 'Saving %', 'Last purchase']
        return table_download(f"medsync_supplier_prices_{timezone.localdate():%Y%m%d}", heads, data, fmt)
    pg = Paginator(rows, 25).get_page(request.GET.get('page'))
    return page(request, 'app/supplier_prices.html', active='supplier_prices', rows=pg, page=pg,
                total=pg.paginator.count, q=q, multi=multi,
                base=urlencode({'q': q, 'multi': '1' if multi else ''}))


@access.perm_required(('view', 'purchase'))
def supplier_prices_product(request, pk):
    """Supplier-wise prices and full purchase price history for one product."""
    product = get_object_or_404(Product.objects.select_related('unit'), pk=pk)
    pack = (product.unit_conversions.select_related('unit').filter(factor__gt=1).order_by('factor').first())
    factor = pack.factor if pack else None
    data = supplier_prices.compare(_price_rows(PurchaseItem.objects.filter(product=product)),
                                   timezone.localdate(), factor, history_limit=100000)
    fmt = request.GET.get('export')
    if fmt in ('xlsx', 'csv'):
        base_name = product.unit.name if product.unit_id else 'base unit'
        heads = ['Date', 'Supplier', 'Invoice', 'Qty', 'Unit', 'Total (incl. GST)', f'Price per {base_name}']
        if pack:
            heads.append(f'Price per {pack.unit.name}')
        heads.append('Change vs previous (same supplier) %')
        rows = []
        for h in data['history']:
            row = [h['date'], h['supplier'], h['invoice'], h['qty'], h['unit'], h['total'], h['rate']]
            if pack:
                row.append((h['rate'] * factor).quantize(Decimal('0.01')))
            row.append(h['change'] if h['change'] is not None else '')
            rows.append(row)
        return table_download(f"medsync_prices_{product.pk}_{timezone.localdate():%Y%m%d}", heads, rows, fmt)
    return page(request, 'app/supplier_prices_product.html', active='supplier_prices', product=product,
                pack=pack, suppliers=data['suppliers'], sm=data['summary'], history=data['history'][:100],
                history_total=data['history_total'], stale_days=supplier_prices.STALE_DAYS,
                best_txt=supplier_prices.money(data['summary']['best']['last_rate']) if data['summary']['best'] else '-',
                worst_txt=supplier_prices.money(data['summary']['worst']['last_rate']) if data['summary']['worst'] else '-')


@access.perm_required(('add', 'purchase'), ('change', 'purchase'), ('view', 'purchase'))
def supplier_price_hint(request):
    """JSON for the Stock Entry form: every supplier's latest price for a product + unit factors, so the form can
    show 'last price from this supplier / cheapest supplier' and compare the price being typed."""
    try:
        pid = int(request.GET.get('product', ''))
    except (TypeError, ValueError):
        return JsonResponse({'found': False})
    product = Product.objects.select_related('unit').filter(pk=pid).first()
    if not product:
        return JsonResponse({'found': False})
    convs = list(product.unit_conversions.select_related('unit').order_by('factor'))
    pack = next((c for c in convs if c.factor > 1), None)
    factors = {str(c.unit_id): c.factor for c in convs}
    if product.unit_id:
        factors[str(product.unit_id)] = 1
    data = supplier_prices.compare(_price_rows(PurchaseItem.objects.filter(product=product)),
                                   timezone.localdate(), pack.factor if pack else None, history_limit=0)
    last = (PurchaseItem.objects.filter(product=product, unit__isnull=False)
            .order_by('-purchase__invoice_date', '-id').values_list('unit_id', flat=True).first())
    return JsonResponse({
        'found': True, 'base_name': product.unit.name if product.unit_id else 'unit',
        'mrp': str(product.mrp) if product.mrp is not None else '',
        # Smart defaults for the next entry: the unit used last time and where the product is kept
        'defaults': dict(unit=str(last) if last and str(last) in factors else '', rack=product.rack or '',
                         row=product.row or '', location=product.location or '',
                         composition=product.composition or ''),
        'pack_name': pack.unit.name if pack else '', 'pack_factor': pack.factor if pack else 0,
        'factors': factors,
        'suppliers': [dict(id=s['supplier_id'], name=s['supplier'], rate=str(s['last_rate']),
                           rate_txt=s['last_rate_txt'], pack_txt=s['last_rate_pack_txt'],
                           date=s['last_date'].strftime('%d %b %y'), stale=s['stale'],
                           best=s['is_best'], current=s['current'])
                      for s in data['suppliers']],
    })
