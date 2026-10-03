import calendar
from urllib.parse import urlencode
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, models, transaction
from django.db.models import F, ProtectedError, Q, Sum
from django.forms import inlineformset_factory, modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from .models import (
    Batch, Bill, Brand, Category, Expense, InvoiceItem, Order, Payment, Product,
    ProductUnit, Purchase, PurchaseItem, PurchaseOrder, PurchaseOrderItem, Sale,
    Supplier, Transaction, Unit,
)

NAV = [
    {'key': 'dashboard', 'url': '/', 'label': 'Dashboard', 'icon': '🏠', 'group': ''},
    {'key': 'bills', 'url': '/bills/', 'label': 'Billing', 'icon': '🧾', 'group': ''},
    {'key': 'products', 'url': '/products/', 'label': 'Products', 'icon': '💊', 'group': ''},
    {'key': 'purchases', 'url': '/purchases/', 'label': 'Purchases', 'icon': '📦', 'group': ''},
    {'key': 'batches', 'url': '/batches/', 'label': 'Batch & Expiry', 'icon': '⏳', 'group': ''},
    {'key': 'sales', 'url': '/sales/', 'label': 'Sales', 'icon': '📈', 'group': 'Accounts'},
    {'key': 'transactions', 'url': '/transactions/', 'label': 'Transactions', 'icon': '🔁', 'group': 'Accounts'},
    {'key': 'orders', 'url': '/orders/', 'label': 'Customer Orders', 'icon': '🛒', 'group': 'Orders'},
    {'key': 'purchase_orders', 'url': '/purchase_orders/', 'label': 'Purchase Orders', 'icon': '📋', 'group': 'Orders'},
    {'key': 'suppliers', 'url': '/suppliers/', 'label': 'Suppliers', 'icon': '🏭', 'group': 'Accounts'},
    {'key': 'payments', 'url': '/payments/', 'label': 'Payments', 'icon': '💳', 'group': 'Accounts'},
    {'key': 'expenses', 'url': '/expenses/', 'label': 'Expenses', 'icon': '💸', 'group': 'Accounts'},
    {'key': 'report', 'url': '/report/', 'label': 'Profit & Loss', 'icon': '📊', 'group': 'Accounts', 'staff': True},
    {'key': 'categories', 'url': '/categories/', 'label': 'Categories', 'icon': '🗂️', 'group': 'Master Data'},
    {'key': 'brands', 'url': '/brands/', 'label': 'Brands', 'icon': '🏷️', 'group': 'Master Data'},
    {'key': 'units', 'url': '/units/', 'label': 'Units', 'icon': '📏', 'group': 'Master Data'},
]


def badge(text, kind):
    return format_html('<span class="badge {}">{}</span>', kind, text)


def d(x):
    return x.strftime('%d %b %Y') if x else '-'


def rs(x):
    return f"₹{(x or 0):,.2f}"


def stock_badge(p):
    if p.stock_qty == 0:
        return badge('Out of stock', 'red')
    if p.stock_qty <= p.reorder_level:
        return badge('Low stock', 'amber')
    return badge('In stock', 'green')


def expiry_badge(b):
    kind = {'Safe': 'green', 'Expiring Soon': 'amber', 'Expired': 'red'}.get(b.expiry_status, 'grey')
    return badge(b.expiry_status, kind)


PO_COLORS = {'PENDING': 'amber', 'SENT': 'blue', 'COMPLETED': 'green', 'CANCELLED': 'red'}

CONFIG = {
    'products': dict(
        title='Products', one='Product', model=Product, order=['name'],
        search=['name', 'composition', 'sku'],
        cols=[('Name', lambda o: o.name), ('Composition', lambda o: o.composition or '-'),
              ('Stock', lambda o: o.stock_display()), ('GST', lambda o: f"{o.gst_rate}%"),
              ('Status', stock_badge)],
        fields=['name', 'composition', 'hsn_code', 'category', 'brand', 'gst_rate', 'unit',
                'reorder_level', 'default_supplier', 'location', 'rack', 'row', 'is_active'],
        inline=dict(model=ProductUnit, fields=['unit', 'factor'],
                    title='Unit conversions (jaise 1 Strip = 15 Tablet)'),
    ),
    'suppliers': dict(
        title='Suppliers', one='Supplier', model=Supplier, order=['company'],
        search=['company', 'gstin', 'phone'],
        cols=[('Company', lambda o: o.company), ('Phone', lambda o: o.phone or '-'),
              ('GSTIN', lambda o: o.gstin or '-'), ('Balance (due)', lambda o: rs(o.opening_balance)),
              ('Active', lambda o: badge('Active', 'green') if o.is_active else badge('Inactive', 'grey'))],
        fields=['company', 'person', 'phone', 'email', 'address', 'city', 'gstin', 'is_active'],
    ),
    'purchases': dict(
        title='Purchases', one='Purchase Invoice', model=Purchase, order=['-invoice_date', '-id'],
        related=['supplier'], search=['invoice_number', 'supplier__company', 'items__product__name'],
        cols=[('Invoice No.', lambda o: o.invoice_number), ('Date', lambda o: d(o.invoice_date)),
              ('Supplier', lambda o: o.supplier.company), ('Items', lambda o: o.total_items),
              ('Total', lambda o: rs(o.total))],
        fields=['supplier', 'invoice_number', 'invoice_date'],
        inline=dict(model=PurchaseItem, title='Invoice items',
                    fields=['product', 'qty', 'unit', 'purchase_price', 'manufacture_date',
                            'expire_date', 'rack', 'row', 'location', 'composition']),
    ),
    'bills': dict(
        title='Billing', one='Bill', model=Bill, order=['-id'],
        search=['customer_name'],
        initial=lambda: {'date': timezone.localdate()},
        cols=[('Bill #', lambda o: f"#{o.id}"), ('Customer', lambda o: o.customer_name),
              ('Date', lambda o: d(o.date)), ('Items', lambda o: o.total_items),
              ('Total', lambda o: rs(o.total))],
        fields=['customer_name', 'date', 'invoice_mode', 'order'],
        inline=dict(model=InvoiceItem, title='Bill items',
                    fields=['product', 'qty', 'unit', 'rate', 'discount_percent', 'discount_amount', 'is_igst']),
    ),
    'payments': dict(
        title='Payments', one='Payment', model=Payment, order=['-id'], related=['supplier'],
        search=['supplier__company'],
        cols=[('Supplier', lambda o: o.supplier.company), ('Amount', lambda o: rs(o.amount)),
              ('Mode', lambda o: o.payment_mode or '-'), ('Date', lambda o: d(o.payment_date)),
              ('Due after', lambda o: rs(o.due))],
        fields=['supplier', 'amount', 'payment_mode'],
    ),
    'expenses': dict(
        title='Expenses', one='Expense', model=Expense, order=['-date', '-id'], search=['name'],
        cols=[('Type', lambda o: o.get_name_display()), ('Amount', lambda o: rs(o.amount)),
              ('Date', lambda o: d(o.date))],
        fields=['name', 'amount', 'date'],
    ),
    'orders': dict(
        title='Customer Orders', one='Order', model=Order, order=['-id'], related=['customer_product', 'customer_unit'],
        search=['order_id', 'customers_name', 'customer_product__name'],
        initial=lambda: {'order_date': timezone.localdate()},
        cols=[('Order ID', lambda o: o.order_id), ('Customer', lambda o: o.customers_name or '-'),
              ('Product', lambda o: o.customer_product.name if o.customer_product else '-'),
              ('Qty', lambda o: f"{o.customer_qty or 0} {o.customer_unit or ''}".strip()),
              ('Rate', lambda o: rs(o.customer_rate)), ('Date', lambda o: d(o.order_date)),
              ('Billed?', lambda o: badge('Billed', 'green') if o.bill_set.exists() else badge('Pending', 'amber'))],
        fields=['customers_name', 'customer_product', 'customer_qty', 'customer_unit', 'customer_rate', 'order_date'],
    ),
    'purchase_orders': dict(
        title='Purchase Orders', one='Purchase Order', model=PurchaseOrder, order=['-id'], related=['supplier'],
        search=['po_number', 'supplier__company', 'items__product__name'],
        cols=[('PO No.', lambda o: o.po_number), ('Supplier', lambda o: o.supplier.company),
              ('Date', lambda o: d(o.date_created)), ('Items', lambda o: o.items.count()),
              ('Status', lambda o: badge(o.get_status_display(), PO_COLORS.get(o.status, 'grey')))],
        action=lambda o: f"/purchases/new/?from_po={o.pk}" if o.status in ('PENDING', 'SENT') else None,
        action_label='📥 Receive',
        fields=['supplier', 'status'],
        inline=dict(model=PurchaseOrderItem, fields=['product', 'order_qty'],
                    title='Items (qty base unit me, jaise Tablet)'),
    ),
    'sales': dict(
        title='Sales', one='Sale', model=Sale, order=['-id'], readonly=True, search=['name'],
        cols=[('Date', lambda o: d(o.date)), ('Customer', lambda o: o.name or '-'),
              ('Mode', lambda o: badge(o.invoice_mode.title(), 'green' if o.invoice_mode == 'online' else 'grey')),
              ('Taxable', lambda o: rs(o.taxable_value)), ('GST', lambda o: rs(o.gst)),
              ('Total', lambda o: rs(o.total))],
    ),
    'transactions': dict(
        title='Transactions', one='Transaction', model=Transaction, order=['-id'], readonly=True,
        search=['supplier', 'customer'],
        cols=[('Type', lambda o: badge('Sale', 'green') if o.sale_amount is not None else badge('Purchase', 'amber')),
              ('Date', lambda o: d(o.sale_date or o.purchase_date)),
              ('Party', lambda o: o.customer or o.supplier or '-'),
              ('Amount', lambda o: ('+ ' + rs(o.sale_amount)) if o.sale_amount is not None
                                   else ('- ' + rs(o.purchase_amount)))],
    ),
    'categories': dict(
        title='Categories', one='Category', model=Category, order=['name'], search=['name'],
        cols=[('Name', lambda o: o.name)], fields=['name'],
    ),
    'brands': dict(
        title='Brands', one='Brand', model=Brand, order=['name'], search=['name'],
        cols=[('Name', lambda o: o.name)], fields=['name'],
    ),
    'units': dict(
        title='Units', one='Unit', model=Unit, order=['name'], search=['name'],
        cols=[('Name', lambda o: o.name)], fields=['name'],
    ),
    'batches': dict(
        title='Batch & Expiry', one='Batch', model=Batch, order=['expire_date', 'id'], readonly=True,
        related=['product'], search=['product__name', 'batch_number'],
        cols=[('Product', lambda o: o.product.name if o.product else '-'),
              ('Batch', lambda o: o.batch_number), ('Qty (base)', lambda o: o.qty),
              ('Expiry', lambda o: d(o.expire_date)), ('Status', expiry_badge)],
    ),
}


PER_OPTS = (10, 25, 50, 100)


def date_cb(field, **kw):
    if isinstance(field, models.DateField):
        kw['widget'] = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
    return field.formfield(**kw)


def page(request, tpl, **ctx):
    ctx['nav'] = NAV
    return render(request, tpl, ctx)


def get_cfg(key):
    cfg = CONFIG.get(key)
    if not cfg:
        raise Http404
    return cfg


@login_required
def dashboard(request):
    today = timezone.localdate()
    soon = today + timedelta(days=30)

    def total(**f):
        return Sale.objects.filter(**f).aggregate(s=Sum('total'))['s'] or 0

    live = Batch.objects.filter(qty__gt=0, expire_date__isnull=False).select_related('product')
    low = Product.objects.filter(stock_qty__lte=F('reorder_level')).order_by('stock_qty')
    expiring = live.filter(expire_date__gte=today, expire_date__lte=soon).order_by('expire_date')
    return page(
        request, 'app/dashboard.html', active='dashboard',
        today_sales=total(date=today),
        month_sales=total(date__month=today.month, date__year=today.year),
        low_count=low.count(), out_count=Product.objects.filter(stock_qty=0).count(),
        expired_count=live.filter(expire_date__lt=today).count(), expiring_count=expiring.count(),
        payable=Supplier.objects.aggregate(s=Sum('opening_balance'))['s'] or 0,
        low_rows=[(p.name, p.stock_display(), stock_badge(p)) for p in low[:6]],
        exp_rows=[(b.product.name if b.product else '-', b.batch_number, b.qty, d(b.expire_date))
                  for b in expiring[:6]],
    )


@login_required
def report(request):
    if not request.user.is_staff:  # profit sirf staff/owner dekh sake
        raise PermissionDenied
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    if not 1 <= month <= 12:
        month = today.month

    def dec(x):
        return Decimal(str(x or 0))

    # Income = sales ki taxable value (GST income nahi hai)
    sales = dec(Sale.objects.filter(date__month=month, date__year=year)
                .aggregate(s=Sum('taxable_value'))['s'])

    # COGS = jo maal bika uski purchase cost (batch_history se)
    batch_rate = {b.id: b.purchase_item.rate for b in
                  Batch.objects.select_related('purchase_item').filter(purchase_item__isnull=False)}
    cogs = Decimal('0')
    for item in InvoiceItem.objects.filter(sale__date__month=month, sale__date__year=year):
        for batch_id, qty in (item.batch_history or {}).items():
            cogs += batch_rate.get(int(batch_id), Decimal('0')) * qty

    purchases = dec(PurchaseItem.objects.filter(
        purchase__invoice_date__month=month, purchase__invoice_date__year=year
    ).aggregate(s=Sum('tax'))['s'])
    expenses = dec(Expense.objects.filter(date__month=month, date__year=year)
                   .aggregate(s=Sum('amount'))['s'])

    gross = sales - cogs
    return page(
        request, 'app/report.html', active='report', month=month, year=year,
        months=[(i, calendar.month_name[i]) for i in range(1, 13)],
        years=list(range(today.year - 5, today.year + 1)),
        month_name=calendar.month_name[month],
        sales=sales, cogs=cogs, gross=gross, expenses=expenses, net=gross - expenses,
        purchases=purchases,
    )


@login_required
def listing(request, key):
    cfg = get_cfg(key)
    qs = cfg['model'].objects.all()
    if cfg.get('related'):
        qs = qs.select_related(*cfg['related'])
    q = request.GET.get('q', '').strip()
    if q and cfg.get('search'):
        cond = Q()
        for f in cfg['search']:
            cond |= Q(**{f + '__icontains': q})
        qs = qs.filter(cond).distinct()
    try:
        per = int(request.GET.get('per', 10))
    except (TypeError, ValueError):
        per = 10
    if per not in PER_OPTS:
        per = 10
    pg = Paginator(qs.order_by(*cfg['order']), per).get_page(request.GET.get('page'))
    act = cfg.get('action')
    rows = [(o.pk, [fn(o) for _, fn in cfg['cols']], act(o) if act else None) for o in pg]
    return page(request, 'app/list.html', key=key, active=key, cfg=cfg, heads=[h for h, _ in cfg['cols']],
                rows=rows, page=pg, q=q, per=per, per_opts=PER_OPTS, total=pg.paginator.count,
                base=urlencode({'q': q, 'per': per}))


@login_required
def edit(request, key, pk=None):
    cfg = get_cfg(key)
    if cfg.get('readonly'):
        raise Http404
    obj = get_object_or_404(cfg['model'], pk=pk) if pk else None
    Form = modelform_factory(cfg['model'], fields=cfg['fields'], formfield_callback=date_cb)
    inl = cfg.get('inline')

    # Purchase Order se "Receive" dabane par invoice pehle se bhara hua khule
    po, po_items = None, []
    po_id = request.GET.get('from_po', '')
    if key == 'purchases' and not obj and po_id.isdigit():
        po = PurchaseOrder.objects.filter(pk=po_id).select_related('supplier').first()
        if po:
            po_items = list(po.items.select_related('product'))

    FS = inlineformset_factory(cfg['model'], inl['model'], fields=inl['fields'],
                               extra=max(1, len(po_items)), can_delete=True,
                               formfield_callback=date_cb) if inl else None

    post = request.POST if request.method == 'POST' else None
    initial = cfg['initial']() if cfg.get('initial') and not obj else None
    if po:
        initial = {'supplier': po.supplier_id}
    form = Form(post, instance=obj, initial=initial)
    # NOTE: POST par initial nahi dete, warna bina badle rows "unchanged" maan ke chhoot jaate hain
    fs_initial = [{'product': i.product_id, 'qty': i.order_qty, 'unit': i.product.unit_id}
                  for i in po_items] if (po and not post) else None
    fs = FS(post, instance=form.instance, initial=fs_initial) if FS else None

    # Stock/transactions ban chuke ho to base unit badalna band
    if key == 'products' and obj and (
        obj.stock_qty or PurchaseItem.objects.filter(product=obj).exists()
        or InvoiceItem.objects.filter(product=obj).exists()
    ):
        form.fields['unit'].disabled = True

    if key == 'bills':  # sirf wahi orders jinka bill nahi bana (+ is bill ka apna order)
        form.fields['order'].queryset = Order.objects.filter(Q(bill__isnull=True) | Q(pk=obj.order_id if obj else None))
        form.fields['order'].label_from_instance = lambda o: (
            f"{o.order_id} · {o.customers_name or '-'} · {o.customer_product or '-'} x {o.customer_qty or 0}")

    if post and form.is_valid() and (fs is None or fs.is_valid()):
        try:
            with transaction.atomic():
                form.save()
                if fs:
                    fs.save()
                if po and po.status != 'CANCELLED':
                    po.status = 'COMPLETED'
                    po.save(update_fields=['status'])
            messages.success(request, f"{cfg['one']} save ho gaya ✅")
            return redirect('list', key=key)
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        except IntegrityError:
            messages.error(request, 'Duplicate entry hai (ye record pehle se maujood hai).')
        if obj is None:  # rollback ke baad instance ko phir "naya" rakho
            form.instance.pk = None
            form.instance._state.adding = True

    title = f"Edit {cfg['one']}" if obj else f"New {cfg['one']}"
    return page(request, 'app/form.html', key=key, active=key, title=title, form=form, fs=fs,
                inline_title=inl['title'] if inl else '',
                note=f"{po.po_number} ({po.supplier.company}) se bhara gaya hai. Invoice number, price, MFG/EXP date bharo. Save karte hi PO Completed ho jayega." if po else '')


@login_required
@require_POST
def delete(request, key, pk):
    cfg = get_cfg(key)
    if cfg.get('readonly'):
        raise Http404
    obj = get_object_or_404(cfg['model'], pk=pk)
    if key == 'units' and (Product.objects.filter(unit=obj).exists()
                           or ProductUnit.objects.filter(unit=obj).exists()):
        messages.error(request, 'Ye unit kisi product me use ho raha hai, isliye delete nahi ho sakta.')
        return redirect('list', key=key)
    try:
        obj.delete()
        messages.success(request, f"{cfg['one']} delete ho gaya.")
    except ProtectedError:
        messages.error(request, 'Ye record kisi aur record se juda hai, isliye delete nahi ho sakta.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('list', key=key)