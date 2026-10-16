"""Look and feel of the admin panel: names, sections (groups) on the home page and the summary cards.
Everything here is cosmetic and fails safe: if anything goes wrong the normal Django admin is shown."""
from datetime import timedelta

from django.contrib import admin
from django.db.models import F, Sum
from django.urls import reverse
from django.utils import timezone

from . import access

admin.site.site_header = 'MedSync Admin'
admin.site.site_title = 'MedSync Admin'
admin.site.index_title = 'Admin home'

# Sections of the home page: (title, key, models by class name). Models that are not listed (Users, Groups) go to the last section.
GROUPS = [
    ('Master data', 'ms_master', ['Category', 'Brand', 'Unit', 'Supplier', 'Product']),
    ('Purchases', 'ms_purchases', ['Purchase', 'PurchaseOrder', 'PurchaseReturn']),
    ('Sales', 'ms_sales', ['Order', 'Bill', 'Sale', 'Customer', 'CustomerPayment', 'SalesReturn']),
    ('Stock', 'ms_stock', ['Batch', 'StockWriteOff', 'StockAudit']),
    ('Accounts', 'ms_accounts', ['Payment', 'Transaction', 'Expense', 'RecurringExpense', 'FinancialReport']),
    ('Settings', 'ms_settings', ['ShopProfile', 'Employee', 'ImportLayout']),
]


def group_apps(apps):
    """Regroup the app list (one entry per Django app) into the sections above. Returns the original list on any problem."""
    models = [m for app in apps for m in app.get('models', [])]
    if not models:
        return apps
    listed = {name for _, _, names in GROUPS for name in names}
    groups = []
    for title, key, names in GROUPS:
        mine = sorted((m for m in models if m.get('object_name') in names), key=lambda m: str(m['name']))
        if mine:
            groups.append((title, key, mine))
    rest = sorted((m for m in models if m.get('object_name') not in listed), key=lambda m: str(m['name']))
    if rest:
        groups.append(('Users & access', 'ms_access', rest))
    return [dict(name=title, app_label=key, app_url=mine[0].get('admin_url') or '', has_module_perms=True, models=mine)
            for title, key, mine in groups]


_original_app_list = admin.site.get_app_list


def get_app_list(request, app_label=None):
    apps = _original_app_list(request, app_label)
    if app_label:
        return apps                      # the page of one app (/admin/backend/) stays as Django makes it
    try:
        return group_apps(apps)
    except Exception:                    # cosmetic only: never break the admin
        return apps


admin.site.get_app_list = get_app_list


def _money(x):
    return f"₹{(x or 0):,.2f}"


def summary_cards(user):
    """Small numbers for the top of the admin home page. A card is shown only to a user who may view that data."""
    from .models import Batch, Product, Sale, Supplier
    today = timezone.localdate()
    cards = []

    def add(label, value, hint, url_name, tone):
        cards.append(dict(label=label, value=value, hint=hint, url=reverse(url_name), tone=tone))

    if access.can(user, 'view', 'sale'):
        total = Sale.objects.filter(date=today).aggregate(s=Sum('total'))['s']
        add('Sales today', _money(total), 'After returns', 'admin:backend_sale_changelist', 'ok')
    if access.can(user, 'view', 'product'):
        n = Product.objects.exclude(is_active=False).filter(stock_qty__lte=F('reorder_level')).count()
        add('Low stock', n, 'Medicines at or below the reorder level', 'admin:backend_product_changelist',
            'bad' if n else 'ok')
    if access.can(user, 'view', 'batch'):
        soon = Batch.objects.filter(qty__gt=0, expire_date__gte=today, expire_date__lte=today + timedelta(days=30)).count()
        gone = Batch.objects.filter(qty__gt=0, expire_date__lt=today).count()
        add('Expiring in 30 days', soon, 'Batches still in stock', 'admin:backend_batch_changelist', 'warn' if soon else 'ok')
        add('Expired', gone, 'Batches to take off the shelf', 'admin:backend_batch_changelist', 'bad' if gone else 'ok')
    if access.can(user, 'view', 'supplier'):
        due = Supplier.objects.aggregate(s=Sum('opening_balance'))['s']
        add('To pay to suppliers', _money(due), 'Total balance', 'admin:backend_supplier_changelist', 'warn' if (due or 0) > 0 else 'ok')
    return cards


_original_index = admin.site.index


def index(request, extra_context=None):
    extra = dict(extra_context or {})
    try:
        extra['medsync_cards'] = summary_cards(request.user)
    except Exception:                    # cosmetic only: never break the admin
        extra['medsync_cards'] = []
    return _original_index(request, extra)


admin.site.index = index
