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

from . import reorder
from .models import Batch, InvoiceItem, Product, PurchaseOrderItem


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
    """refresh_all, but only the first time it is asked on a day. Never raises (a cache problem must not break a page)."""
    today = today or timezone.localdate()
    try:
        if not cache.add(f"levels-{today:%Y%m%d}", 1, 60 * 60 * 26):
            return 0
        return refresh_all(today)
    except Exception:       # noqa: BLE001
        return 0


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
