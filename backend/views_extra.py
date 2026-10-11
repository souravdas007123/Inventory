"""Views for the newer features: customer ledger, returns, write-off, GST/drug reports, barcode lookup,
alerts, Purchase Order share, stock audit."""
import calendar
import re
from datetime import date as date_cls, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import auditlog, backup, notify, reorder, reports, summary, supplier_prices
from .models import (
    Batch, Bill, Brand, Category, Customer, InvoiceItem, Product, ProductUnit, Purchase, PurchaseItem, PurchaseOrder,
    PurchaseOrderItem, PurchaseReturn, PurchaseReturnItem, SalesReturn, Supplier, Unit, SalesReturnItem, ShopProfile, StockAudit, StockAuditItem, StockWriteOff,
)
from .imports import GST_ALLOWED, _fk, table_download
from .views import page


def staff_required(view):
    """Staff/owner only (together with login_required)."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


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

@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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

@login_required
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


SCHEDULE_FILTERS = {'H1X': ('H1', 'X'), 'H': ('H',), 'ALL': ('H', 'H1', 'X')}


@login_required
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


@login_required
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
# QUICK ADD PRODUCT (from the purchase form)
# ======================================================

@login_required
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


@login_required
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
    reorder_raw = post.get('reorder_level', '').strip()
    if reorder_raw and not reorder_raw.isdigit():
        return fail('Reorder level must be a whole number.')

    try:
        with transaction.atomic():
            product = Product(
                name=name, composition=post.get('composition', '').strip()[:255] or None, gst_rate=gst, unit=unit,
                schedule=schedule, barcode=barcode, reorder_level=int(reorder_raw) if reorder_raw else 5,
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


@login_required
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


@login_required
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
    qs = qs.select_related('unit').prefetch_related('unit_conversions__unit').order_by('name')[:8]
    results = [dict(id=p.pk, name=p.name or str(p), composition=p.composition or '', barcode=p.barcode or '',
                    mrp=str(p.mrp) if p.mrp is not None else '', rate1=_bill_rate(p), factors=_unit_factors(p),
                    stock=p.stock_display(), stock_qty=p.stock_qty, schedule=p.schedule) for p in qs]
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
    """product id -> supplier id: the product's default supplier, else the cheapest current supplier,
    else the supplier of the last purchase (same rule as the automatic purchase order)."""
    by_product = {}
    for r in supplier_prices.rows_from_items(PurchaseItem.objects.filter(product_id__in=product_ids)):
        by_product.setdefault(r['product_id'], []).append(r)
    out = {}
    for pid in product_ids:
        sid = defaults.get(pid)
        rows = by_product.get(pid, [])
        if not sid and rows:
            sid = supplier_prices.cheapest_supplier_id(rows, today)
        if not sid and rows:
            sid = max(rows, key=lambda r: (r['date'], r['id']))['supplier_id']
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


@login_required
def reorder_view(request):
    today = timezone.localdate()
    if request.method == 'POST':
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


@login_required
def po_print(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier'), pk=pk)
    items = po.items.select_related('product', 'product__unit')
    return render(request, 'app/po_print.html', dict(po=po, items=items, shop=ShopProfile.get()))


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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
