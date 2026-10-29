"""
Automatic reorder level. Nobody types it in: it follows the real sales of the last 30 days
(about 10 days of average sales, at least 5, in whole packs). See `reorder.auto_level`.

It is refreshed
  - for one product, every time that product is sold (just before the automatic purchase order is decided),
  - for all products, once a day (first visit of the day to the dashboard / products / reorder page, and `daily_tasks`).
A product with no sales in the last 30 days keeps its current level.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

import logging

from django.db import transaction
from django.db.models import F

from . import reorder, supplier_prices
from .models import Batch, InvoiceItem, Product, PurchaseItem, PurchaseOrder, PurchaseOrderItem, Supplier

log = logging.getLogger(__name__)


def _pack_factor(product):
    packs = [c.factor for c in product.unit_conversions.all() if c.factor and c.factor > 1]
    return max(packs) if packs else 0


def _sold(since, product_id=None):
    qs = InvoiceItem.objects.filter(bill__status='FINAL', bill__date__gte=since, product__isnull=False)
    if product_id:
        return qs.filter(product_id=product_id).aggregate(s=Sum('base_qty'))['s'] or 0
    return {r['product_id']: r['s'] for r in qs.values('product_id').annotate(s=Sum('base_qty'))}


def level_for(product, today=None):
    """The automatic level of one product, or None when it has no sales (keep the current one)."""
    today = today or timezone.localdate()
    sold = _sold(today - timedelta(days=reorder.LEVEL_WINDOW), product.pk)
    return reorder.auto_level(sold, pack_factor=_pack_factor(product))


def refresh_product(product, today=None):
    """Brings the reorder level of this product in line with its sales. Returns the level in use."""
    level = level_for(product, today)
    if level is not None and level != product.reorder_level:
        product.reorder_level = level
        product.save(update_fields=['reorder_level'])
    return product.reorder_level


def refresh_all(today=None):
    """All active products. Returns how many levels changed."""
    today = today or timezone.localdate()
    sold = _sold(today - timedelta(days=reorder.LEVEL_WINDOW))
    changed = []
    for p in Product.objects.exclude(is_active=False).filter(pk__in=list(sold)).prefetch_related('unit_conversions'):
        level = reorder.auto_level(sold.get(p.pk, 0), pack_factor=_pack_factor(p))
        if level is not None and level != p.reorder_level:
            p.reorder_level = level
            changed.append(p)
    if changed:
        Product.objects.bulk_update(changed, ['reorder_level'], batch_size=500)
    return len(changed)


def refresh_daily(today=None):
    """refresh_all, but only the first time it is asked on a day; and the sweep for low stock (see `sweep`), at most once an hour.
    Never raises (a cache problem must not break a page)."""
    today = today or timezone.localdate()
    changed = 0
    try:
        if cache.add(f"levels-{today:%Y%m%d}", 1, 60 * 60 * 26):
            changed = refresh_all(today)
    except Exception:       # noqa: BLE001
        log.exception('Reorder levels could not be refreshed')
    sweep_hourly(today)
    return changed


def order_quantity(product, today=None):
    """How many base units the automatic purchase order should ask for (same rule as the Reorder Suggestions page)."""
    today = today or timezone.localdate()
    sold = _sold(today - timedelta(days=reorder.LEVEL_WINDOW), product.pk)
    expired = Batch.objects.filter(product=product, qty__gt=0, expire_date__lt=today).aggregate(s=Sum('qty'))['s'] or 0
    on_order = PurchaseOrderItem.objects.filter(
        product=product, purchase_order__status__in=['PENDING', 'SENT']).aggregate(s=Sum('order_qty'))['s'] or 0
    position = max(product.stock_qty - expired, 0) + on_order
    daily = Decimal(sold) / reorder.LEVEL_WINDOW
    return reorder.order_qty(daily, position, level=product.reorder_level, pack_factor=_pack_factor(product))


def maybe_create_order(product, today=None):
    """The automatic purchase order of ONE product. Called right after a sale, and by `sweep` for products that became low
    in another way. Returns True when the product was added to a purchase order.

    Nothing is made when: the stock is above the reorder level; no supplier is known; the product is already on a PENDING or SENT
    order; or nothing is needed."""
    today = today or timezone.localdate()
    # The reorder level follows the real sales (it cannot be typed in), then the stock is compared with it.
    refresh_product(product, today)
    if product.stock_qty > product.reorder_level:
        return False
    # Supplier: the one whose latest price for this product is the lowest; else the product's default supplier;
    # else the supplier of the last purchase (see supplier_prices.pick_supplier).
    rows = supplier_prices.rows_from_items(PurchaseItem.objects.filter(product=product))
    supplier_id = supplier_prices.pick_supplier(rows, product.default_supplier_id, today)
    supplier = Supplier.objects.filter(pk=supplier_id).first() if supplier_id else None
    if not supplier:
        return False
    # If a PENDING or SENT PO already exists, do not create a new one
    if PurchaseOrderItem.objects.filter(product=product, purchase_order__status__in=['PENDING', 'SENT']).exists():
        return False
    # Quantity: the same rule as the Reorder Suggestions page (30 days of sales, whole packs)
    qty = order_quantity(product, today)
    if qty <= 0:
        return False
    po = PurchaseOrder.objects.filter(supplier=supplier, status='PENDING').first() or PurchaseOrder.objects.create(supplier=supplier)
    PurchaseOrderItem.objects.create(purchase_order=po, product=product, order_qty=qty)
    return True


def sweep(today=None, limit=200):
    """Adds every product that is low right now to a purchase order, the same way a sale does. A sale only checks the product it
    sold, so a product that became low another way (a write-off, an expiry or purchase return, a stock count, an edited purchase)
    would wait for its next sale. Only products that SOLD in the last 30 days are looked at, so a new shop whose imported
    products have no stock yet does not get a flood of orders. Returns how many products were added."""
    today = today or timezone.localdate()
    sold = _sold(today - timedelta(days=reorder.LEVEL_WINDOW))
    low = (Product.objects.exclude(is_active=False).filter(pk__in=list(sold), stock_qty__lte=F('reorder_level'))
           .exclude(pk__in=PurchaseOrderItem.objects.filter(purchase_order__status__in=['PENDING', 'SENT']).values('product_id'))
           .prefetch_related('unit_conversions').order_by('stock_qty', 'pk')[:limit])
    made = 0
    for product in low:
        try:
            with transaction.atomic():            # one product that fails must not stop the others
                made += 1 if maybe_create_order(product, today) else 0
        except Exception:                          # noqa: BLE001
            log.exception('Automatic purchase order failed for product %s', product.pk)
    return made


def sweep_hourly(today=None):
    """`sweep`, but at most once an hour. Never raises."""
    try:
        if not cache.add('auto-orders-sweep', 1, 60 * 60):
            return 0
        return sweep(today)
    except Exception:       # noqa: BLE001
        log.exception('The sweep for low stock failed')
        return 0
