"""Fast / slow / dead stock: which medicines sell quickly and which ones hold your money.

For every active medicine that has stock, over the chosen period (default 90 days):
  sold      = quantity billed in FINAL bills (base units, as billed: returns are not taken off)
  per day   = sold / period
  cover     = stock / per day: how many days the stock lasts at that pace (None when nothing sold)
  Fast      = sold and the stock lasts 30 days or less
  Normal    = sold and the stock lasts 31 to 120 days
  Slow      = sold, but the stock lasts more than 120 days
  Dead      = nothing sold in the period
  New       = nothing sold yet, but all the stock came in inside the period (too early to call it dead)
Value = what the stock cost (purchase rate of each batch x quantity left)."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Sum
from django.utils import timezone

from .models import Batch, InvoiceItem, Product

PERIODS = (30, 60, 90, 180)
DEFAULT_PERIOD = 90
FAST_COVER, SLOW_COVER = 30, 120
CLASSES = ('fast', 'normal', 'slow', 'dead', 'new')
LABELS = dict(fast='Fast', normal='Normal', slow='Slow', dead='Dead', new='New')


def period_from(raw):
    return int(raw) if str(raw).isdecimal() and int(raw) in PERIODS else DEFAULT_PERIOD


def classify(sold, stock, days, newest_arrival, today):
    """The class of one medicine. `newest_arrival` is the date its OLDEST live stock came in (None if unknown)."""
    if sold > 0:
        per_day = Decimal(sold) / Decimal(days)
        cover = Decimal(stock) / per_day
        return ('fast' if cover <= FAST_COVER else 'normal' if cover <= SLOW_COVER else 'slow'), cover
    if newest_arrival is not None and newest_arrival > today - timedelta(days=days):
        return 'new', None
    return 'dead', None


def build(days=DEFAULT_PERIOD, today=None):
    """{'rows': [...], 'totals': {class: {'count', 'value'}}, 'days': days}. Rows: dead first, biggest value first."""
    today = today or timezone.localdate()
    since = today - timedelta(days=days - 1)
    final = InvoiceItem.objects.filter(bill__status='FINAL', product__isnull=False)
    sold = dict(final.filter(bill__date__gte=since, bill__date__lte=today).values_list('product_id')
                .annotate(s=Sum('base_qty')).order_by())
    last = dict(final.values_list('product_id').annotate(d=Max('bill__date')).order_by())
    products = list(Product.objects.exclude(is_active=False).filter(stock_qty__gt=0).select_related('unit')
                    .prefetch_related('unit_conversions__unit'))
    value, expiry, arrived = {}, {}, {}
    for b in (Batch.objects.filter(product_id__in=[p.pk for p in products], qty__gt=0)
              .select_related('purchase_item', 'purchase_item__purchase')):
        item = b.purchase_item
        if item is not None and item.rate:
            value[b.product_id] = value.get(b.product_id, Decimal('0')) + Decimal(item.rate) * b.qty
        if b.expire_date and (b.product_id not in expiry or b.expire_date < expiry[b.product_id]):
            expiry[b.product_id] = b.expire_date
        when = item.purchase.invoice_date if item is not None and item.purchase_id else None
        if when is not None and (b.product_id not in arrived or when < arrived[b.product_id]):
            arrived[b.product_id] = when
    rows, totals = [], {c: dict(count=0, value=Decimal('0')) for c in CLASSES}
    for p in products:
        s = int(sold.get(p.pk) or 0)
        cls, cover = classify(s, p.stock_qty, days, arrived.get(p.pk), today)
        v = value.get(p.pk)
        rows.append(dict(id=p.pk, name=p.name or str(p), stock=p.stock_display(), stock_qty=p.stock_qty, sold=s,
                         per_day=(Decimal(s) / days) if s else None, cover=int(cover) if cover is not None else None,
                         last_sold=last.get(p.pk), value=v, expiry=expiry.get(p.pk), cls=cls, label=LABELS[cls]))
        totals[cls]['count'] += 1
        totals[cls]['value'] += v or Decimal('0')
    order = {c: i for i, c in enumerate(('dead', 'slow', 'new', 'normal', 'fast'))}
    rows.sort(key=lambda r: (order[r['cls']], -(r['value'] or 0), r['name'].lower()))
    return dict(rows=rows, totals=totals, days=days, since=since, today=today)
