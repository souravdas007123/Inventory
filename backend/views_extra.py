"""Views for the newer features: customer ledger, returns, write-off, GST/drug reports, barcode lookup,
alerts, Purchase Order share, stock audit."""
import calendar
from datetime import date as date_cls
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import auditlog, backup, notify, reports, supplier_prices
from .models import (
    Batch, Bill, Customer, Product, Purchase, PurchaseItem, PurchaseOrder, PurchaseReturn, PurchaseReturnItem,
    SalesReturn, SalesReturnItem, ShopProfile, StockAudit, StockAuditItem, StockWriteOff,
)
from .imports import table_download
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

@login_required
def product_lookup(request):
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': [], 'exact': False})
    qs = (Product.objects.exclude(is_active=False)
          .filter(Q(barcode=q) | Q(sku__iexact=q) | Q(name__icontains=q) | Q(composition__icontains=q))
          .select_related('unit').prefetch_related('unit_conversions__unit').order_by('name')[:8])
    results = [dict(id=p.pk, name=p.name or str(p), composition=p.composition or '', barcode=p.barcode or '',
                    mrp=str(p.mrp) if p.mrp is not None else '', stock=p.stock_display(), stock_qty=p.stock_qty,
                    schedule=p.schedule) for p in qs]
    exact = any(r['barcode'] == q for r in results)
    return JsonResponse({'results': results, 'exact': exact})


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
    return JsonResponse({
        'found': True, 'base_name': product.unit.name if product.unit_id else 'unit',
        'pack_name': pack.unit.name if pack else '', 'pack_factor': pack.factor if pack else 0,
        'factors': factors,
        'suppliers': [dict(id=s['supplier_id'], name=s['supplier'], rate=str(s['last_rate']),
                           rate_txt=s['last_rate_txt'], pack_txt=s['last_rate_pack_txt'],
                           date=s['last_date'].strftime('%d %b %y'), stale=s['stale'],
                           best=s['is_best'], current=s['current'])
                      for s in data['suppliers']],
    })
