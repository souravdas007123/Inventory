import calendar
import logging
from urllib.parse import urlencode
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

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
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_POST

from . import access, auditlog, backup, checks, levels, notify, recurring, supplier_prices
from .models import (
    AuditLog, Batch, Bill, Brand, Category, Customer, CustomerPayment, Employee, Expense, InvoiceItem, Order, Payment,
    Product, ProductUnit, Purchase, PurchaseItem, PurchaseOrder, PurchaseOrderItem, PurchaseReturn, RecurringExpense,
    Sale, SalesReturn, ShopProfile, StockAudit, StockWriteOff, Supplier, Transaction, Unit,
)
from .imports import export_value, table_download
from .reports import dashboard_charts, dashboard_overview, profit_and_loss

IMPORT_KINDS = {'products': 'products', 'customers': 'customers', 'suppliers': 'suppliers', 'purchases': 'stock',
                'categories': 'categories', 'brands': 'brands', 'units': 'units', 'expenses': 'expenses'}

NAV = [
    {'key': 'dashboard', 'url': '/', 'label': 'Dashboard', 'icon': '🏠', 'group': ''},
    {'key': 'bills', 'url': '/bills/', 'label': 'Billing', 'icon': '🧾', 'group': ''},
    {'key': 'products', 'url': '/products/', 'label': 'Products', 'icon': '🧴', 'group': ''},
    {'key': 'purchases', 'url': '/purchases/', 'label': 'Purchases', 'icon': '📦', 'group': ''},
    {'key': 'batches', 'url': '/batches/', 'label': 'Batch & Expiry', 'icon': '⏳', 'group': ''},
    {'key': 'customers', 'url': '/customers/', 'label': 'Customers (Credit)', 'icon': '👥', 'group': 'Customers'},
    {'key': 'customer_payments', 'url': '/customer_payments/', 'label': 'Credit Receipts', 'icon': '💰', 'group': 'Customers'},
    {'key': 'orders', 'url': '/orders/', 'label': 'Customer Orders', 'icon': '🛒', 'group': 'Customers'},
    {'key': 'refill', 'url': '/refill/', 'label': 'Refill Reminders', 'icon': '💊', 'group': 'Customers'},
    {'key': 'sales_returns', 'url': '/sales_returns/', 'label': 'Sales Returns', 'icon': '↩️', 'group': 'Customers'},
    {'key': 'suppliers', 'url': '/suppliers/', 'label': 'Suppliers', 'icon': '🏭', 'group': 'Suppliers'},
    {'key': 'purchase_orders', 'url': '/purchase_orders/', 'label': 'Purchase Orders', 'icon': '📋', 'group': 'Suppliers'},
    {'key': 'reorder', 'url': '/reorder/', 'label': 'Reorder Suggestions', 'icon': '🔄', 'group': 'Suppliers'},
    {'key': 'supplier_prices', 'url': '/supplier_prices/', 'label': 'Supplier Prices', 'icon': '⚖️', 'group': 'Suppliers'},
    {'key': 'payments', 'url': '/payments/', 'label': 'Supplier Payments', 'icon': '💳', 'group': 'Suppliers'},
    {'key': 'purchase_returns', 'url': '/purchase_returns/', 'label': 'Purchase Returns', 'icon': '📤', 'group': 'Suppliers'},
    {'key': 'expiry', 'url': '/expiry/', 'label': 'Expiry Returns', 'icon': '⚠️', 'group': 'Suppliers'},
    {'key': 'sales', 'url': '/sales/', 'label': 'Sales', 'icon': '📈', 'group': 'Accounts'},
    {'key': 'transactions', 'url': '/transactions/', 'label': 'Transactions', 'icon': '🔁', 'group': 'Accounts'},
    {'key': 'expenses', 'url': '/expenses/', 'label': 'Expenses', 'icon': '💸', 'group': 'Accounts'},
    {'key': 'fixed_expenses', 'url': '/fixed_expenses/', 'label': 'Fixed Expenses', 'icon': '📆', 'group': 'Accounts'},
    {'key': 'employees', 'url': '/employees/', 'label': 'Employees', 'icon': '👨‍⚕️', 'group': 'Accounts'},
    {'key': 'salary', 'url': '/salary/', 'label': 'Salary Sheet', 'icon': '💼', 'group': 'Accounts'},
    {'key': 'report', 'url': '/report/', 'label': 'Profit & Loss', 'icon': '📊', 'group': 'Accounts', 'staff': True},
    {'key': 'gst', 'url': '/gst/', 'label': 'GST Reports', 'icon': '🧮', 'group': 'Accounts', 'staff': True},
    {'key': 'drug_register', 'url': '/drug_register/', 'label': 'Drug Register (H/H1)', 'icon': '📒', 'group': 'Stock'},
    {'key': 'writeoffs', 'url': '/writeoffs/', 'label': 'Stock Write-off', 'icon': '🗑️', 'group': 'Stock'},
    {'key': 'stock_audits', 'url': '/stock_audits/', 'label': 'Stock Audit', 'icon': '🔎', 'group': 'Stock', 'staff': True},
    {'key': 'categories', 'url': '/categories/', 'label': 'Categories', 'icon': '🗂️', 'group': 'Master Data'},
    {'key': 'brands', 'url': '/brands/', 'label': 'Brands', 'icon': '🏷️', 'group': 'Master Data'},
    {'key': 'units', 'url': '/units/', 'label': 'Units', 'icon': '📏', 'group': 'Master Data'},
    {'key': 'import', 'url': '/import/', 'label': 'Import / Export', 'icon': '📥', 'group': 'Master Data'},
    {'key': 'audit', 'url': '/audit/', 'label': 'Audit Log', 'icon': '🕵️', 'group': 'Master Data', 'staff': True},
    {'key': 'backup', 'url': '/backup/', 'label': 'Backup', 'icon': '💾', 'group': 'Master Data', 'staff': True},
    {'key': 'shop', 'url': '/shop/', 'label': 'Shop Settings', 'icon': '🏪', 'group': 'Master Data', 'staff': True},
]


def paid_this_month(emp):
    today = timezone.localdate()
    return sum((e.amount for e in emp.expenses.all()
                if e.name == 'SALARY' and e.date.year == today.year and e.date.month == today.month), Decimal('0'))


def auto_levels():
    """Once a day: brings every product's reorder level in line with its sales (cheap, never raises)."""
    levels.refresh_daily()


def post_recurring(request):
    """Adds the fixed expenses / salaries that are due (safe to call often) and tells the user if any were added."""
    try:
        made = recurring.post_due()
    except Exception:       # never let this break the page the user opened
        logging.getLogger(__name__).exception('Could not add the recurring expenses')
        return 0
    if made:
        messages.info(request, f"{made} fixed expense / salary entr{'y was' if made == 1 else 'ies were'} added automatically.")
    return made


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


def pick_batch_txt(p):
    b = p.get_next_batch()
    return f"{b.batch_number} ({d(b.expire_date)})" if b else '-'


def alt_txt(p):
    if p.stock_qty > 0:
        return '-'
    alts = p.get_alternatives()
    return ', '.join(f"{a.name} ({a.stock_qty})" for a in alts) if alts else 'No alternative'


def expiry_badge(b):
    kind = {'Safe': 'green', 'Expiring Soon': 'amber', 'Expired': 'red'}.get(b.expiry_status, 'grey')
    return badge(b.expiry_status, kind)


def balance_cell(c):
    bal = c.balance()
    return badge(rs(bal), 'red') if bal > 0 else rs(bal)


def po_actions(o):
    acts = []
    if o.status in ('PENDING', 'SENT'):
        acts.append(dict(label='📥 Receive', url=f"/purchases/new/?from_po={o.pk}"))
    acts += [
        dict(label='🖨️ Print', url=f"/purchase_orders/{o.pk}/print/", target='_blank'),
        dict(label='💬 WhatsApp', url=f"/purchase_orders/{o.pk}/whatsapp/", target='_blank'),
        dict(label='✉️ Email', url=f"/purchase_orders/{o.pk}/email/", post=True,
             confirm='Send an email to the supplier?'),
    ]
    return acts


def batch_actions(o):
    if o.qty > 0 and o.expiry_status == 'Expired':
        return [dict(label='🗑️ Write-off', url=f"/batches/{o.pk}/writeoff/", post=True, cls='red',
                     confirm='Write off all remaining stock of this batch? (It will be counted as a loss in the P&L)')]
    return []


def stack(first, *small):
    """A compact multi-line cell for lists: the first line as is, then small grey lines (empty ones are skipped)."""
    lines = [format_html('<div>{}</div>', first)]
    lines += [format_html('<small class="sub">{}</small>', x) for x in small if x and x != '-']
    return format_html_join('', '{}', ((x,) for x in lines))


def product_cell(o):
    sched = format_html(' {}', badge(o.schedule, 'amber')) if o.schedule and o.schedule != 'NONE' else ''
    return stack(format_html('<b>{}</b>{}', o.name or '-', sched), o.composition,
                 ' \u00b7 '.join(str(x) for x in (o.brand, o.category) if x))


def bill_gst(o):
    return o.cgst + o.sgst + o.igst


def bill_mode_cell(o):
    mode = badge(o.invoice_mode.title(), 'green' if o.invoice_mode == 'online' else 'grey')
    return stack(o.customer_name, o.order.order_id if o.order_id else '', mode)


def bill_amount_cell(o):
    return stack(format_html('<b>{}</b>', rs(o.total)), f"Taxable {rs(o.taxable)}", f"GST {rs(bill_gst(o))}")


def gst_txt(o):
    """'18%' (not '18.00%'); a rate like 2.5 stays '2.5%'."""
    return f"{format(Decimal(o.gst_rate or 0).normalize(), 'f')}%"


def product_price_cell(o):
    unit = f" / {o.unit}" if o.unit_id else ''
    return stack(f"{rs(o.mrp)}{unit}" if o.mrp is not None else '-', f"GST {gst_txt(o)}")


def prepare_products(objs):
    """One query for the page: the best (cheapest current) supplier of every product shown."""
    today = timezone.localdate()
    by_product = {}
    for r in supplier_prices.rows_from_items(PurchaseItem.objects.filter(product_id__in=[o.pk for o in objs])):
        by_product.setdefault(r['product_id'], []).append(r)
    for o in objs:
        o.best_offer = supplier_prices.best_offer(by_product.get(o.pk, []), today)


def product_supplier_cell(o):
    offer, default = getattr(o, 'best_offer', None), o.default_supplier
    if offer:
        unit = f" / {o.unit}" if o.unit_id else ''
        extra = f"Default: {default.company}" if default and default.pk != offer['supplier_id'] else ''
        return stack(format_html('<b>{}</b>', offer['supplier']),
                     f"{supplier_prices.money(offer['rate'])}{unit} \u00b7 {d(offer['date'])}", extra,
                     f"{offer['suppliers']} suppliers" if offer['suppliers'] > 1 else '')
    if default:
        return stack(format_html('<b>{}</b>', default.company), 'Default supplier, not bought yet')
    return badge('No supplier yet', 'amber')


def product_place_cell(o):
    place = ' \u00b7 '.join(x for x in (f"Rack {o.rack}" if o.rack else '', f"Row {o.row}" if o.row else '') if x) or '-'
    batch = pick_batch_txt(o)
    return stack(place, f"Sell first: {batch}" if batch != '-' else '')


def product_extra_cell(o):
    alt = alt_txt(o)
    return stack(f"HSN {o.hsn_code}" if o.hsn_code else '-', (o.sku if str(o.sku).upper().startswith('SKU') else f"SKU {o.sku}") if o.sku else '',
                 f"Alternative: {alt}" if alt != '-' else '')


PO_COLORS = {'PENDING': 'amber', 'SENT': 'blue', 'COMPLETED': 'green', 'CANCELLED': 'red'}

def product_names(o, limit=3):
    """'Dolo 650, Crocin +2 more' for the Purchases list."""
    names = [i.product.name for i in o.items.all()]
    return ', '.join(names[:limit]) + (f' +{len(names) - limit} more' if len(names) > limit else '') if names else '-'


CONFIG = {
    'products': dict(
        title='Products', one='Product', model=Product, order=['name'], no_new=True,    # products are created from Purchases
        related=['brand', 'category', 'unit', 'default_supplier'], prefetch=['unit_conversions__unit', 'batches'],
        search=['name', 'composition', 'sku', 'hsn_code', 'barcode'],
        cols=[('Name', lambda o: o.name), ('Composition', lambda o: o.composition or '-'),
              ('Brand', lambda o: o.brand or '-'), ('Category', lambda o: o.category or '-'),
              ('HSN', lambda o: o.hsn_code or '-'), ('SKU', lambda o: o.sku or '-'),
              ('Rack / Row', lambda o: f"{o.rack or '-'} / {o.row or '-'}"),
              ('Unit', lambda o: o.unit or '-'), ('GST', gst_txt),
              ('MRP (per unit)', lambda o: rs(o.mrp) if o.mrp is not None else '-'),
              ('Schedule', lambda o: '-' if o.schedule == 'NONE' else badge(o.schedule, 'amber')),
              ('Stock', lambda o: o.stock_display()), ('Pick Batch (FEFO)', pick_batch_txt),
              ('Status', stock_badge), ('Alternative', alt_txt)],
        # The screen shows fewer, multi-line columns (no side scroll); the Excel / CSV export keeps every column above.
        list_cols=[('Product', product_cell), ('Stock', lambda o: stack(o.stock_display(), stock_badge(o), f"Reorder at {o.reorder_level}")),
                   ('Price', product_price_cell), ('Supplier', product_supplier_cell),
                   ('Place / sell first', product_place_cell), ('Other', product_extra_cell)],
        prepare=prepare_products,
        fields=['name', 'composition', 'hsn_code', 'category', 'brand', 'gst_rate', 'unit', 'mrp', 'barcode',
                'schedule', 'default_supplier', 'location', 'rack', 'row', 'is_active'],
        inline=dict(model=ProductUnit, fields=['unit', 'factor'],
                    title='Unit conversions (e.g. 1 Strip = 15 Tablet)'),
        actions=lambda o: [dict(label='⚖️ Prices', url=f'/supplier_prices/{o.pk}/')],
    ),
    'suppliers': dict(
        title='Suppliers', one='Supplier', model=Supplier, order=['company'],
        search=['company', 'gstin', 'phone'],
        cols=[('Company', lambda o: o.company), ('Phone', lambda o: o.phone or '-'),
              ('Address', lambda o: o.address or '-'), ('GSTIN', lambda o: o.gstin or '-'),
              ('State', lambda o: o.state or '-'), ('PAN', lambda o: o.pan or '-'),
              ('Balance (due)', lambda o: rs(o.opening_balance)), ('Created', lambda o: d(o.created_at)),
              ('Active', lambda o: badge('Active', 'green') if o.is_active else badge('Inactive', 'grey'))],
        fields=['company', 'person', 'phone', 'email', 'address', 'city', 'gstin', 'is_active'],
    ),
    'purchases': dict(
        title='Purchases', one='Purchase Invoice', model=Purchase, order=['-invoice_date', '-id'],
        related=['supplier'], prefetch=['items__product'],
        search=['invoice_number', 'supplier__company', 'items__product__name'],
        cols=[('Invoice No.', lambda o: o.invoice_number), ('Date', lambda o: d(o.invoice_date)),
              ('Supplier', lambda o: o.supplier.company), ('Products', product_names),
              ('Items', lambda o: o.total_items),
              ('Taxable', lambda o: rs(o.taxable)), ('GST', lambda o: rs(o.gst_total)),
              ('Total', lambda o: rs(o.total))],
        fields=['supplier', 'invoice_number', 'invoice_date'],
        actions=lambda o: [dict(label='↩ Return', url=f"/purchases/{o.pk}/return/")],
        inline=dict(model=PurchaseItem, title='Invoice items',
                    fields=['product', 'qty', 'unit', 'purchase_price', 'selling_price', 'manufacture_date',
                            'expire_date', 'rack', 'row', 'location', 'composition']),
    ),
    'bills': dict(
        title='Billing', one='Bill', model=Bill, order=['-id'],
        related=['order'], prefetch=['items'], search=['customer_name', 'invoice_number'],
        initial=lambda: {'date': timezone.localdate()},
        cols=[('Invoice No.', lambda o: o.display_number), ('Customer', lambda o: o.customer_name),
              ('Mode', lambda o: badge(o.invoice_mode.title(), 'green' if o.invoice_mode == 'online' else 'grey')),
              ('Order', lambda o: o.order.order_id if o.order_id else '-'),
              ('Date', lambda o: d(o.date)), ('Items', lambda o: o.total_items),
              ('Taxable', lambda o: rs(o.taxable)), ('CGST', lambda o: rs(o.cgst)),
              ('SGST', lambda o: rs(o.sgst)), ('IGST', lambda o: rs(o.igst)),
              ('Total', lambda o: rs(o.total)),
              ('Payment', lambda o: badge(o.get_payment_mode_display(), 'amber' if o.payment_mode == 'CREDIT' else 'grey')),
              ('Status', lambda o: badge('Final', 'green') if o.status == 'FINAL' else badge('Cancelled', 'red'))],
        # Screen: 6 compact columns (no side scroll). The Excel / CSV export keeps every column above.
        list_cols=[('Invoice', lambda o: stack(format_html('<b>{}</b>', o.display_number), d(o.date))),
                   ('Customer', bill_mode_cell), ('Items', lambda o: o.total_items), ('Amount', bill_amount_cell),
                   ('Payment', lambda o: badge(o.get_payment_mode_display(), 'amber' if o.payment_mode == 'CREDIT' else 'grey')),
                   ('Status', lambda o: badge('Final', 'green') if o.status == 'FINAL' else badge('Cancelled', 'red'))],
        fields=['customer_name', 'customer', 'date', 'invoice_mode', 'order', 'payment_mode', 'amount_paid',
                'doctor_name', 'doctor_reg_no', 'patient_name', 'patient_address'],
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
        title='Expenses', one='Expense', model=Expense, order=['-date', '-id'], related=['employee'],
        search=['name', 'note', 'employee__name', 'employee__code'],
        cols=[('Date', lambda o: d(o.date)), ('Type', lambda o: o.get_name_display()),
              ('Employee', lambda o: str(o.employee) if o.employee_id else '-'),
              ('Amount', lambda o: rs(o.amount)), ('Note', lambda o: o.note or '-'),
              ('Added', lambda o: badge('Automatic', 'blue') if o.auto else 'Manual')],
        fields=['name', 'employee', 'amount', 'date', 'note'],
    ),
    'fixed_expenses': dict(
        title='Fixed Expenses', one='Fixed Expense', model=RecurringExpense, order=['-is_active', 'title'],
        search=['title'],
        hint='Enter a fixed monthly cost (rent, internet ...) only once. An expense entry is added automatically on the '
             'chosen day of every month. If a month\'s amount is different, just edit that entry in Expenses.',
        cols=[('Title', lambda o: o.title), ('Type', lambda o: o.get_name_display()), ('Amount', lambda o: rs(o.amount)),
              ('Day', lambda o: o.day_of_month), ('From', lambda o: d(o.start_date)),
              ('Until', lambda o: d(o.end_date) if o.end_date else 'No end'),
              ('Next entry', lambda o: d(recurring.next_post(o.start_date, o.end_date, o.day_of_month, o.last_period))
               if o.is_active else '-'),
              ('Status', lambda o: badge('Active', 'green') if o.is_active else badge('Stopped', 'grey'))],
        fields=['title', 'name', 'amount', 'day_of_month', 'start_date', 'end_date', 'is_active'],
    ),
    'employees': dict(
        title='Employees', one='Employee', model=Employee, order=['-is_active', 'name'], prefetch=['expenses'],
        search=['name', 'code', 'phone', 'designation'],
        hint='Each salary is added to Expenses automatically on the employee\'s pay day. An employee with salary entries '
             'cannot be deleted: untick Active (and set the leaving date) instead.',
        cols=[('ID', lambda o: o.code), ('Name', lambda o: o.name), ('Designation', lambda o: o.designation or '-'),
              ('Phone', lambda o: o.phone or '-'), ('Salary / month', lambda o: rs(o.monthly_salary)),
              ('Pay day', lambda o: o.pay_day), ('Paid this month', lambda o: rs(paid_this_month(o))),
              ('Status', lambda o: badge('Working', 'green') if o.is_active else badge('Left', 'grey'))],
        fields=['name', 'phone', 'designation', 'joining_date', 'leaving_date', 'monthly_salary', 'pay_day',
                'auto_salary', 'is_active'],
        actions=lambda o: [dict(label='💰 Add salary', url=f"/expenses/new/?employee={o.pk}"),
                           dict(label='📒 History', url=f"/expenses/?q={o.code}")],
    ),
    'orders': dict(
        title='Customer Orders', one='Order', model=Order, order=['-id'], related=['customer_product', 'customer_unit'],
        prefetch=['bill_set'],
        search=['order_id', 'customers_name', 'customer_product__name'],
        initial=lambda: {'order_date': timezone.localdate()},
        cols=[('Order ID', lambda o: o.order_id), ('Customer', lambda o: o.customers_name or '-'),
              ('Product', lambda o: o.customer_product.name if o.customer_product else '-'),
              ('Qty', lambda o: f"{o.customer_qty or 0} {o.customer_unit or ''}".strip()),
              ('Rate', lambda o: rs(o.customer_rate)), ('Date', lambda o: d(o.order_date)),
              ('Billed?', lambda o: badge('Billed', 'green') if o.bill_set.all() else badge('Pending', 'amber'))],
        fields=['customers_name', 'customer_product', 'customer_qty', 'customer_unit', 'customer_rate', 'order_date'],
    ),
    'purchase_orders': dict(
        title='Purchase Orders', one='Purchase Order', model=PurchaseOrder, order=['-id'], related=['supplier'],
        prefetch=['items'],
        search=['po_number', 'supplier__company', 'items__product__name'],
        cols=[('PO No.', lambda o: o.po_number), ('Supplier', lambda o: o.supplier.company),
              ('Date', lambda o: d(o.date_created)), ('Items', lambda o: len(o.items.all())),
              ('Status', lambda o: badge(o.get_status_display(), PO_COLORS.get(o.status, 'grey')))],
        actions=po_actions,
        fields=['supplier', 'status'],
        inline=dict(model=PurchaseOrderItem, fields=['product', 'order_qty'],
                    title='Items (qty in base unit, e.g. Tablet)'),
    ),
    'sales': dict(
        title='Sales', one='Sale', model=Sale, order=['-id'], readonly=True, search=['name'],
        cols=[('Date', lambda o: d(o.date)), ('Customer', lambda o: o.name or '-'),
              ('Mode', lambda o: badge('Return', 'red') if o.sales_return_item_id
                  else badge(o.invoice_mode.title(), 'green' if o.invoice_mode == 'online' else 'grey')),
              ('Taxable', lambda o: rs(o.taxable_value)), ('GST', lambda o: rs(o.gst)),
              ('Total', lambda o: rs(o.total))],
    ),
    'transactions': dict(
        title='Transactions', one='Transaction', model=Transaction, order=['-id'], readonly=True,
        search=['supplier', 'customer'],
        cols=[('Type', lambda o: (badge('Sale Return', 'red') if o.sale_amount < 0 else badge('Sale', 'green'))
                  if o.sale_amount is not None
                  else (badge('Purchase Return', 'red') if (o.purchase_amount or 0) < 0 else badge('Purchase', 'amber'))),
              ('Date', lambda o: d(o.sale_date or o.purchase_date)),
              ('Party', lambda o: o.customer or o.supplier or '-'),
              ('Amount', lambda o: ('+ ' + rs(o.sale_amount)) if o.sale_amount is not None
                                   else ('- ' + rs(o.purchase_amount)))],   # amount is shown as negative for returns
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
        related=['product', 'supplier', 'unit'], search=['product__name', 'batch_number', 'supplier__company'],
        cols=[('Product', lambda o: o.product.name if o.product else '-'),
              ('Supplier', lambda o: o.supplier.company if o.supplier else '-'),
              ('Batch', lambda o: o.batch_number), ('Qty (base)', lambda o: o.qty),
              ('Unit', lambda o: o.unit or '-'),
              ('Rack / Row', lambda o: f"{o.rack or '-'} / {o.row or '-'}"),
              ('Location', lambda o: o.location or '-'),
              ('MFG', lambda o: d(o.manufacture_date)), ('Expiry', lambda o: d(o.expire_date)),
              ('Status', expiry_badge)],
        actions=batch_actions,
    ),

    # ---------------- Customers / credit ----------------
    'customers': dict(
        title='Customers (Credit)', one='Customer', model=Customer, order=['name'], search=['name', 'phone', 'gstin'],
        cols=[('Name', lambda o: o.name), ('Phone', lambda o: o.phone or '-'), ('GSTIN', lambda o: o.gstin or '-'),
              ('Credit limit', lambda o: rs(o.credit_limit) if o.credit_limit else 'No limit'),
              ('Credit due', balance_cell),
              ('Active', lambda o: badge('Active', 'green') if o.is_active else badge('Inactive', 'grey'))],
        fields=['name', 'phone', 'address', 'gstin', 'credit_limit', 'is_active'],
        actions=lambda o: [dict(label='📒 Ledger', url=f"/customers/{o.pk}/ledger/"),
                           dict(label='+ Receipt', url=f"/customer_payments/new/?customer={o.pk}")],
    ),
    'customer_payments': dict(
        title='Credit Receipts', one='Receipt', model=CustomerPayment, order=['-date', '-id'], related=['customer'],
        search=['customer__name', 'note'], initial=lambda: {'date': timezone.localdate()},
        cols=[('Date', lambda o: d(o.date)), ('Customer', lambda o: o.customer.name),
              ('Amount', lambda o: rs(o.amount)), ('Mode', lambda o: o.get_mode_display()),
              ('Note', lambda o: o.note or '-')],
        fields=['customer', 'amount', 'mode', 'date', 'note'],
    ),

    # ---------------- Returns / write-off / audit ----------------
    'sales_returns': dict(
        title='Sales Returns (Credit Notes)', one='Sales Return', model=SalesReturn, order=['-id'],
        related=['bill'], prefetch=['items'], no_new=True, no_edit=True,
        hint='To create a new return, click "↩ Return" next to a bill in the Billing list.',
        search=['note_number', 'bill__invoice_number', 'bill__customer_name'],
        cols=[('Credit Note', lambda o: o.note_number), ('Date', lambda o: d(o.date)),
              ('Against bill', lambda o: o.bill.display_number), ('Customer', lambda o: o.bill.customer_name),
              ('Items', lambda o: o.total_items), ('Taxable', lambda o: rs(o.taxable)),
              ('Total', lambda o: rs(o.total)), ('Refund', lambda o: o.get_refund_mode_display())],
        actions=lambda o: [dict(label='🖨️ Note', url=f"/sales_returns/{o.pk}/note/", target='_blank')],
    ),
    'purchase_returns': dict(
        title='Purchase Returns (Debit Notes)', one='Purchase Return', model=PurchaseReturn, order=['-id'],
        related=['purchase', 'purchase__supplier'], prefetch=['items'], no_new=True, no_edit=True,
        hint='To create a new return, click "↩ Return" next to an invoice in the Purchases list.',
        search=['note_number', 'purchase__invoice_number', 'purchase__supplier__company'],
        cols=[('Debit Note', lambda o: o.note_number), ('Date', lambda o: d(o.date)),
              ('Purchase invoice', lambda o: o.purchase.invoice_number),
              ('Supplier', lambda o: o.purchase.supplier.company), ('Items', lambda o: o.total_items),
              ('Taxable', lambda o: rs(o.taxable)), ('Total', lambda o: rs(o.total))],
        actions=lambda o: [dict(label='🖨️ Note', url=f"/purchase_returns/{o.pk}/note/", target='_blank')],
    ),
    'writeoffs': dict(
        title='Stock Write-off (Expired / Damaged)', one='Write-off', model=StockWriteOff, order=['-id'],
        related=['batch', 'batch__product'], no_edit=True,
        hint='You can also write off the full stock of an expired batch in one click from the Batch & Expiry list.',
        search=['batch__product__name', 'batch__batch_number'], initial=lambda: {'date': timezone.localdate()},
        cols=[('Date', lambda o: d(o.date)),
              ('Product', lambda o: o.batch.product.name if o.batch.product else '-'),
              ('Batch', lambda o: o.batch.batch_number), ('Qty (base)', lambda o: o.qty),
              ('Reason', lambda o: o.get_reason_display()), ('Cost (loss)', lambda o: rs(o.cost)),
              ('Note', lambda o: o.note or '-')],
        fields=['batch', 'qty', 'reason', 'date', 'note'],
    ),
    'audit': dict(
        title='Audit Log (who changed what)', one='Log', model=AuditLog, order=['-id'], readonly=True, staff=True,
        search=['user', 'action', 'target', 'detail'],
        cols=[('When', lambda o: timezone.localtime(o.created_at).strftime('%d %b %Y, %I:%M %p')),
              ('User', lambda o: o.user or '-'), ('Action', lambda o: o.action),
              ('Target', lambda o: o.target or '-'), ('Detail', lambda o: o.detail or '-')],
        fields=[],
    ),
    'stock_audits': dict(
        title='Physical Stock Audit', one='Stock Audit', model=StockAudit, order=['-id'], readonly=True, staff=True,
        prefetch=['items'], start_url='/stock_audits/start/',
        hint='Starting an audit takes a snapshot of the current stock. Count the stock in the shop, enter the counts, then click Apply.',
        cols=[('#', lambda o: o.pk), ('Date', lambda o: d(o.date)), ('Note', lambda o: o.note or '-'),
              ('Status', lambda o: badge('Applied', 'green') if o.status == 'APPLIED' else badge('Open', 'amber')),
              ('Counted', lambda o: f"{sum(1 for i in o.items.all() if i.counted_qty is not None)} / {len(o.items.all())}")],
        actions=lambda o: [dict(label='🔎 Open', url=f"/stock_audits/{o.pk}/count/")],
    ),
}


PER_OPTS = (10, 25, 50, 100)


def date_cb(field, **kw):
    if isinstance(field, models.DateField):
        kw['widget'] = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
    return field.formfield(**kw)


def page(request, tpl, **ctx):
    return render(request, tpl, ctx)         # the menu comes from access.template_context


def nav_for(user):
    """The menu entries this user may open (owner: all; others: by their model permissions)."""
    out = []
    for n in NAV:
        rule, also_add = access.NAV_RULES.get(n['key']), None
        if rule is None and n['key'] in CONFIG:
            model = CONFIG[n['key']]['model']._meta.model_name
            rule, also_add = ('view', model), ('add', model)       # add-only users still get the entry (opens the form)
        if n.get('staff'):
            rule = 'owner'
        if rule == 'owner':
            ok = access.is_owner(user)
        else:
            ok = bool(rule) and (access.can(user, *rule) or bool(also_add and access.can(user, *also_add)))
        if ok:
            out.append(n)
    return out


def get_cfg(key, request=None, action=None):
    cfg = CONFIG.get(key)
    if not cfg:
        raise Http404
    if request is not None:
        if cfg.get('staff') and not access.is_owner(request.user):
            raise PermissionDenied
        if action and not access.can(request.user, action, cfg['model']._meta.model_name):
            raise PermissionDenied
    return cfg


def backup_warning(max_days=3):
    """Text for the dashboard when the database has not been backed up for a while (staff only), else ''."""
    try:
        found = backup.list_backups()
    except OSError:
        return ''
    if not found:
        return 'No backup has been taken yet.'
    from datetime import datetime
    age = (datetime.now() - found[0]['when']).days   # both are naive local times (backup.list_backups uses mtime)
    return f"The last backup was {age} days ago." if age >= max_days else ''


@login_required
def dashboard(request):
    if not access.can(request.user, 'view', 'sale'):      # e.g. a purchase clerk: go to the first page they may use
        first = next((n['url'] for n in nav_for(request.user) if n['key'] != 'dashboard'), None)
        if first:
            return redirect(first)
        raise PermissionDenied
    post_recurring(request)
    auto_levels()
    today = timezone.localdate()
    soon = today + timedelta(days=30)

    def total(**f):
        return Sale.objects.filter(**f).aggregate(s=Sum('total'))['s'] or 0

    live = Batch.objects.filter(qty__gt=0, expire_date__isnull=False).select_related('product')
    active = Product.objects.exclude(is_active=False)
    low = active.filter(stock_qty__lte=F('reorder_level')).order_by('stock_qty')
    backup_note = backup_warning() if access.is_owner(request.user) else ''
    expiring = live.filter(expire_date__gte=today, expire_date__lte=soon).order_by('expire_date')
    return page(
        request, 'app/dashboard.html', active='dashboard',
        today_sales=total(date=today),
        month_sales=total(date__month=today.month, date__year=today.year),
        backup_note=backup_note,
        low_count=low.count(), out_count=active.filter(stock_qty=0).count(),
        expired_count=live.filter(expire_date__lt=today).count(), expiring_count=expiring.count(),
        payable=Supplier.objects.aggregate(s=Sum('opening_balance'))['s'] or 0,
        low_rows=[(p.name, p.stock_display(), stock_badge(p)) for p in low[:6]],
        exp_rows=[(b.product.name if b.product else '-', b.batch_number, b.qty, d(b.expire_date))
                  for b in expiring[:6]],
        charts=dashboard_charts(today),
        ov=dashboard_overview(today, include_money=access.is_owner(request.user)),
    )


@login_required
def report(request):
    if not access.is_owner(request.user):  # only the owner can see profit
        raise PermissionDenied
    today = timezone.localdate()
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    if not 1 <= month <= 12:
        month = today.month

    pl = profit_and_loss(month, year)
    return page(
        request, 'app/report.html', active='report', month=month, year=year,
        months=[(i, calendar.month_name[i]) for i in range(1, 13)],
        years=list(range(today.year - 5, today.year + 1)),
        month_name=calendar.month_name[month],
        **pl,
    )


@login_required
def listing(request, key):
    if key in CONFIG and not CONFIG[key].get('readonly') and not CONFIG[key].get('no_new'):
        model = CONFIG[key]['model']._meta.model_name
        if (not access.can(request.user, 'view', model) and access.can(request.user, 'add', model)
                and not CONFIG[key].get('staff')):
            return redirect('new', key=key)          # may add but not view: open the form instead of the list
    cfg = get_cfg(key, request, 'view')
    if key in ('expenses', 'employees', 'fixed_expenses'):
        post_recurring(request)
    if key == 'products':
        auto_levels()
    qs = cfg['model'].objects.all()
    if cfg.get('related'):
        qs = qs.select_related(*cfg['related'])
    if cfg.get('prefetch'):
        qs = qs.prefetch_related(*cfg['prefetch'])
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
    fmt = request.GET.get('export')
    if fmt in ('xlsx', 'csv'):   # the whole list (filtered by search) as Excel/CSV
        rows = [[export_value(fn(o)) for _, fn in cfg['cols']] for o in qs.order_by(*cfg['order'])[:20000]]
        return table_download(f"medsync_{key}_{timezone.localdate():%Y%m%d}", [h for h, _ in cfg['cols']], rows, fmt)
    pg = Paginator(qs.order_by(*cfg['order']), per).get_page(request.GET.get('page'))
    summary_line = ''
    if key == 'expenses':       # this month's total and its split by type
        t = timezone.localdate()
        names = dict(Expense.EXPENSES_TYPES)
        parts = list(Expense.objects.filter(date__year=t.year, date__month=t.month).values('name')
                     .annotate(s=Sum('amount')).order_by('-s'))
        if parts:
            summary_line = (f"This month ({t:%b %Y}): {rs(sum(p['s'] for p in parts))} \u00b7 "
                            + ', '.join(f"{names.get(p['name'], p['name'])} {rs(p['s'])}" for p in parts))
    acts = cfg.get('actions')
    if cfg.get('prepare'):
        cfg['prepare'](list(pg))                        # one query for the whole page (e.g. the suppliers of the products)
    shown = cfg.get('list_cols') or cfg['cols']          # on screen; the export above always uses all columns
    rows = [dict(pk=o.pk, obj=o, cells=[fn(o) for _, fn in shown], acts=acts(o) if acts else []) for o in pg]
    return page(request, 'app/list.html', key=key, active=key, cfg=cfg, heads=[h for h, _ in shown], summary_line=summary_line,
                rows=rows, page=pg, q=q, per=per, per_opts=PER_OPTS, total=pg.paginator.count,
                has_actions=bool(acts), import_kind=IMPORT_KINDS.get(key) if access.is_owner(request.user) else None,
                can_add=access.can(request.user, 'add', cfg['model']._meta.model_name),
                can_change=access.can(request.user, 'change', cfg['model']._meta.model_name),
                can_delete=access.can(request.user, 'delete', cfg['model']._meta.model_name),
                can_return=access.can(request.user, 'add', 'salesreturn'),
                base=urlencode({'q': q, 'per': per}))


SCHEDULE_NEEDS_DOCTOR = ('H', 'H1', 'X')
SCHEDULE_NEEDS_REGISTER = ('H1', 'X')


def bill_form_errors(form, fs):
    """Extra checks before a bill is saved (items, Schedule H/H1/X documents). Returns a list of error messages."""
    errors = []
    cd = form.cleaned_data
    if cd.get('invoice_mode') == 'online':
        order = cd.get('order')
        products = [order.customer_product] if order and order.customer_product_id else []
    else:
        products = [f.cleaned_data['product'] for f in fs.forms
                    if getattr(f, 'cleaned_data', None) and not f.cleaned_data.get('DELETE')
                    and f.cleaned_data.get('product')]
        if not products:
            errors.append('A bill needs at least one item.')

    needs_doctor = [p for p in products if p.schedule in SCHEDULE_NEEDS_DOCTOR]
    if needs_doctor:
        names = ', '.join(sorted({p.name or str(p) for p in needs_doctor}))
        if not (cd.get('doctor_name') or '').strip():
            errors.append(f"Schedule H/H1/X medicine ({names}): Doctor name is required.")
        if any(p.schedule in SCHEDULE_NEEDS_REGISTER for p in products):
            if not (cd.get('doctor_reg_no') or '').strip():
                errors.append('Schedule H1/X medicine: Doctor Registration No. is required.')
            if not (cd.get('patient_name') or '').strip():
                errors.append('Schedule H1/X medicine: Patient name is required.')
    return errors


def po_shortfall(po_items, purchase):
    """'Dolo 650 (ordered 100, received 60), ...' for the order lines that were not fully received on this purchase."""
    got = {}
    for it in purchase.items.all():
        got[it.product_id] = got.get(it.product_id, 0) + (it.base_qty or 0)
    return ', '.join(f"{i.product.name or i.product} (ordered {i.order_qty}, received {got.get(i.product_id, 0)})"
                     for i in po_items if got.get(i.product_id, 0) < i.order_qty)


def purchase_check_warnings(fs, obj):
    """Soft warnings for a purchase invoice (odd price / quantity / EXP date). The user can still save."""
    lines = []
    for f in fs.forms:
        cd = getattr(f, 'cleaned_data', None)
        if not cd or cd.get('DELETE') or not cd.get('product'):
            continue
        p, qty = cd['product'], cd.get('qty') or 0
        try:
            factor = p.unit_factor(cd.get('unit'))
        except ValidationError:
            factor = 1
        lines.append(dict(product_id=p.pk, name=p.name or str(p), qty=qty, base_qty=qty * factor,
                          total=cd.get('purchase_price'), selling_price=cd.get('selling_price'),
                          expire_date=cd.get('expire_date')))
    if not lines:
        return []
    old = PurchaseItem.objects.filter(product_id__in={ln['product_id'] for ln in lines})
    if obj is not None:
        old = old.exclude(purchase=obj)
    history = {}
    for r in supplier_prices.rows_from_items(old):
        history.setdefault(r['product_id'], []).append(r)
    return checks.purchase_warnings(lines, history, timezone.localdate())


@login_required
def edit(request, key, pk=None):
    cfg = get_cfg(key, request, 'change' if pk else 'add')
    if cfg.get('readonly') or (cfg.get('no_new') and not pk) or (cfg.get('no_edit') and pk):
        raise Http404
    obj = get_object_or_404(cfg['model'], pk=pk) if pk else None
    if key == 'bills' and obj:  # A Final bill cannot be edited (GST record). If there is a mistake, cancel it and create a new one.
        messages.warning(request, 'This bill is final and cannot be edited. If there is a mistake, cancel it and create a new bill.')
        return redirect('invoice', pk=obj.pk)
    Form = modelform_factory(cfg['model'], fields=cfg['fields'], formfield_callback=date_cb)
    inl = cfg.get('inline')

    # Clicking "Receive" on a Purchase Order opens the invoice pre-filled
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
    if key == 'expenses' and not obj and request.GET.get('employee', '').isdigit():   # "Add salary" on the Employees list
        emp = Employee.objects.filter(pk=int(request.GET['employee']), is_active=True).first()
        if emp:
            initial = {**(initial or {}), 'name': 'SALARY', 'employee': emp.pk, 'amount': emp.monthly_salary,
                       'note': f"Salary {timezone.localdate():%b %Y}"}
    if key == 'customer_payments' and not obj and request.GET.get('customer', '').isdigit():
        initial = {**(initial or {}), 'customer': int(request.GET['customer'])}   # Customers list se "+ Receipt"
    if po:
        initial = {'supplier': po.supplier_id}
    form = Form(post, instance=obj, initial=initial)
    # NOTE: do not pass initial on POST, otherwise unchanged rows are treated as "unchanged" and skipped
    fs_initial = [{'product': i.product_id, 'qty': i.order_qty, 'unit': i.product.unit_id}
                  for i in po_items] if (po and not post) else None
    fs = FS(post, instance=form.instance, initial=fs_initial) if FS else None

    # Block changing the base unit once stock/transactions exist
    if key == 'products' and obj and (
        obj.stock_qty or PurchaseItem.objects.filter(product=obj).exists()
        or InvoiceItem.objects.filter(product=obj).exists()
    ):
        form.fields['unit'].disabled = True

    if key == 'writeoffs':  # only batches that still have stock
        form.fields['batch'].queryset = Batch.objects.filter(qty__gt=0).select_related('product').order_by('expire_date', 'id')
        form.fields['batch'].label_from_instance = lambda b: (
            f"{b.product.name if b.product else '-'} · {b.batch_number} · exp {d(b.expire_date)} · qty {b.qty}")

    if key == 'bills':
        form.fields['customer'].queryset = Customer.objects.filter(is_active=True).order_by('name')

    if key == 'expenses':   # only working employees (plus the one already on this entry)
        form.fields['employee'].queryset = Employee.objects.filter(
            Q(is_active=True) | Q(pk=obj.employee_id if obj else None)).order_by('name')

    if key == 'bills':  # only orders that have no bill yet (+ this bill's own order)
        form.fields['order'].queryset = Order.objects.filter(Q(bill__isnull=True) | Q(pk=obj.order_id if obj else None))
        form.fields['order'].label_from_instance = lambda o: (
            f"{o.order_id} · {o.customers_name or '-'} · {o.customer_product or '-'} x {o.customer_qty or 0}")

    warnings = []
    if post and form.is_valid() and (fs is None or fs.is_valid()):
        problems = bill_form_errors(form, fs) if key == 'bills' else []
        for problem in problems:
            form.add_error(None, problem)
        if key == 'purchases' and not problems:
            warnings = purchase_check_warnings(fs, obj)
        if not problems and not (warnings and not request.POST.get('confirm_warnings')):
            try:
                with transaction.atomic():
                    form.save()
                    if fs:
                        fs.save()
                    if key == 'bills':
                        form.instance.after_items_saved()  # checks such as credit limit (rolled back on failure)
                    if po and po.status != 'CANCELLED':
                        po.status = 'COMPLETED'
                        po.save(update_fields=['status'])
                auditlog.record(request, 'EDIT' if obj else 'CREATE', cfg['one'], str(form.instance))
                messages.success(request, f"{cfg['one']} saved ✅")
                if po and po.status == 'COMPLETED':
                    short = po_shortfall(po_items, form.instance)
                    if short:
                        messages.warning(request, f"{po.po_number} is marked received, but these were short: {short}. Make a new order if you still need them.")
                if key == 'bills' and not obj:  # new bill -> go straight to the invoice
                    return redirect('invoice', pk=form.instance.pk)
                return redirect('list', key=key)
            except ValidationError as e:
                messages.error(request, ' '.join(e.messages))
            except ProtectedError:
                messages.error(request, 'This record is linked to another record (such as a return or write-off), so it cannot be changed.')
            except IntegrityError:
                messages.error(request, 'Duplicate entry (this record already exists).')
            if obj is None:  # after a rollback, treat the instance as "new" again
                form.instance.pk = None
                form.instance._state.adding = True

    title = f"Edit {cfg['one']}" if obj else f"New {cfg['one']}"
    quick = {}
    if key == 'expenses':      # lets the form fill the amount from the employee's salary
        quick = dict(emp_salary={e.pk: str(e.monthly_salary) for e in form.fields['employee'].queryset})
    if key == 'purchases' and access.can(request.user, 'add', 'product'):   # lists for the "New product" window on the purchase screen
        quick = dict(can_add_product=True, np_units=list(Unit.objects.order_by('name').values('id', 'name')),
                     np_cats=list(Category.objects.order_by('name').values_list('name', flat=True)[:500]),
                     np_brands=list(Brand.objects.order_by('name').values_list('name', flat=True)[:500]),
                     np_schedules=Product.SCHEDULE_CHOICES)
    if key == 'purchases' and po:      # banner: what this invoice is receiving
        quick['po'] = dict(number=po.po_number, supplier=po.supplier.company,
                           lines=', '.join(f"{i.product.name or i.product} x {i.order_qty}" for i in po_items))
    if key == 'purchases':
        quick['no_products'] = not Product.objects.exists()       # shows a hint: no import needed, create products right here
    return page(request, 'app/form.html', key=key, active=key, title=title, form=form, fs=fs, back_url=f'/{key}/',
                inline_title=inl['title'] if inl else '', warnings=warnings, **quick,
                note=f"{po.po_number} ({po.supplier.company}) has been pre-filled. Enter the invoice number, price and MFG/EXP dates. The PO will be marked Completed when you save." if po else '')


@login_required
@require_POST
def delete(request, key, pk):
    cfg = get_cfg(key, request, 'delete')
    if cfg.get('readonly'):
        raise Http404
    obj = get_object_or_404(cfg['model'], pk=pk)
    if key == 'bills':
        messages.error(request, 'A bill cannot be deleted (the invoice number record must be kept). Please use Cancel instead.')
        return redirect('list', key=key)
    if key == 'units' and (Product.objects.filter(unit=obj).exists()
                           or ProductUnit.objects.filter(unit=obj).exists()):
        messages.error(request, 'This unit is used by a product, so it cannot be deleted.')
        return redirect('list', key=key)
    label = str(obj)
    try:
        obj.delete()
        auditlog.record(request, 'DELETE', cfg['one'], label)
        messages.success(request, f"{cfg['one']} deleted.")
    except ProtectedError:
        messages.error(request, 'This record is linked to another record, so it cannot be deleted.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('list', key=key)


# ---------------- Invoice / Shop ----------------

ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve',
        'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two(n):
    return ONES[n] if n < 20 else TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else '')


def _three(n):
    h, r = divmod(n, 100)
    return (ONES[h] + ' Hundred' + (' ' if r else '') if h else '') + (_two(r) if r else '')


def rupees_in_words(n):
    """Amount in words using the Indian system (Lakh/Crore)."""
    if n == 0:
        return 'Zero'
    parts = []
    crore, n = divmod(n, 10 ** 7)
    if crore:
        parts.append(rupees_in_words(crore) + ' Crore')
    lakh, n = divmod(n, 10 ** 5)
    if lakh:
        parts.append(_two(lakh) + ' Lakh')
    thousand, n = divmod(n, 1000)
    if thousand:
        parts.append(_two(thousand) + ' Thousand')
    if n:
        parts.append(_three(n))
    return ' '.join(parts)


@access.perm_required(('view', 'bill'), ('add', 'bill'))
def invoice(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    items = list(bill.items.select_related('product', 'unit'))
    batch_map = Batch.objects.in_bulk({int(k) for it in items for k in (it.batch_history or {})})

    rows, zero = [], Decimal('0')
    for n, it in enumerate(items, 1):
        batches = []
        for k in (it.batch_history or {}):
            b = batch_map.get(int(k))
            if b:
                batches.append(f"{b.batch_number} / {b.expire_date.strftime('%m-%y') if b.expire_date else '-'}")
        gross = (it.rate or zero) * (it.qty or 0)
        rows.append(dict(
            n=n, name=it.product.name if it.product else '-',
            comp=(it.product.composition or '') if it.product else '',
            hsn=(it.product.hsn_code or '') if it.product else '',
            batch=', '.join(batches), qty=f"{it.qty} {it.unit or ''}".strip(),
            rate=it.rate or zero, disc=(gross - it.taxable_value).quantize(Decimal('0.01')),
            taxable=it.taxable_value, gst_rate=it.gst_rate, total=it.total or zero,
        ))

    def s(field):
        return sum((getattr(i, field) or zero for i in items), zero)

    total = s('total')
    grand = total.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return render(request, 'app/invoice.html', dict(
        bill=bill, shop=ShopProfile.get(), rows=rows,
        taxable=s('taxable_value'), cgst=s('cgst'), sgst=s('sgst'), igst=s('igst'),
        total=total, round_off=grand - total, grand=grand,
        wa_link=notify.whatsapp_link(
            f"{ShopProfile.get().name} - Bill {bill.display_number} ({bill.date:%d %b %Y}): Total \u20b9{grand}. Thank you!",
            bill.customer.phone if bill.customer_id else ''),
        words=rupees_in_words(int(grand)),
        fmt='thermal' if request.GET.get('fmt') == 'thermal' else 'a4',
    ))


@login_required
@require_POST
def bill_cancel(request, pk):
    if not access.is_owner(request.user):
        raise PermissionDenied
    bill = get_object_or_404(Bill, pk=pk)
    try:
        bill.cancel()
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('list', key='bills')
    auditlog.record(request, 'CANCEL', 'Bill', f"{bill.display_number} · {bill.customer_name}")
    messages.success(request, f"{bill.display_number} cancelled. Stock has been added back.")
    return redirect('list', key='bills')


@login_required
def shop(request):
    if not access.is_owner(request.user):
        raise PermissionDenied
    Form = modelform_factory(ShopProfile, fields=['name', 'address', 'phone', 'alert_phone', 'email', 'gstin',
                                                  'drug_license', 'terms'])
    form = Form(request.POST or None, instance=ShopProfile.get())
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Shop details saved ✅')
        return redirect('shop')
    return page(request, 'app/form.html', key='shop', active='shop', title='Shop Settings',
                form=form, fs=None, back_url='/')