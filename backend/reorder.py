"""
Reorder suggestions from real sales speed. Django-free so it can be tested on its own.

For every product: units sold per day over the last `days` days -> how many days the sellable stock will last ->
how much to order so that about `cover` days of stock are available. Stock already on a pending/sent purchase order
counts as available. Quantities are in the product's base unit and are rounded up to whole packs when the
product has a pack (e.g. Strip).

A product is a dict: id, name, unit, stock_qty, reorder_level, pack_name, pack_factor.
"""
from decimal import Decimal, ROUND_CEILING

LEVEL_DAYS = 10           # the automatic reorder level = this many days of average sales (supplier delivery + safety)
LEVEL_WINDOW = 30         # ... average of the sales of the last this many days
MIN_LEVEL = 5             # ... but never below this (base units)
ORDER_COVER = 30          # an automatic order brings the stock up to this many days of sales

ORDER_NOW_DAYS = 7        # stock lasts less than this many days -> order now
ORDER_SOON_DAYS = 15      # ... less than this -> order soon


def _ceil(x):
    return int(Decimal(x).to_integral_value(rounding=ROUND_CEILING))


def _round_up_to_pack(qty, factor):
    if factor and factor > 1 and qty > 0:
        return _ceil(Decimal(qty) / factor) * factor
    return qty


def auto_level(sold, window=LEVEL_WINDOW, level_days=LEVEL_DAYS, pack_factor=0, floor=MIN_LEVEL):
    """The automatic reorder level (base units): about `level_days` of average sales, at least `floor`, rounded up to
    whole packs. Returns None when nothing was sold in the window, meaning "leave the level as it is"."""
    sold = Decimal(sold or 0)
    if sold <= 0:
        return None
    level = max(_ceil(sold / max(int(window), 1) * level_days), floor)
    return int(_round_up_to_pack(level, pack_factor))


def needed(daily, position, cover=ORDER_COVER, level=0):
    """How many base units to order. `position` = sellable stock + stock already on order.
    With sales: enough for `cover` days of sales. Without sales: twice the reorder level."""
    if daily > 0:
        return _ceil(daily * cover) - position
    return level * 2 - position


def order_qty(daily, position, cover=ORDER_COVER, level=0, pack_factor=0):
    """`needed`, rounded up to whole packs (0 when nothing is needed)."""
    need = needed(daily, position, cover, level)
    return _round_up_to_pack(need, pack_factor) if need > 0 else 0


def suggest(products, sold, expired, on_order, days=30, cover=30):
    """`sold`, `expired`, `on_order` map product id -> base qty. Returns suggestion rows, most urgent first."""
    days, cover = max(int(days), 1), max(int(cover), 1)
    out = []
    for p in products:
        pid = p['id']
        sold_q = Decimal(sold.get(pid, 0) or 0)
        sellable = max(int(p['stock_qty']) - int(expired.get(pid, 0) or 0), 0)
        ordered = int(on_order.get(pid, 0) or 0)
        position = sellable + ordered
        daily = sold_q / days
        days_left = (Decimal(sellable) / daily) if daily > 0 else None

        if daily > 0:
            need = needed(daily, position, cover)
            if sellable == 0:
                status, cls = 'Out of stock', 'red'
            elif days_left < ORDER_NOW_DAYS:
                status, cls = 'Order now', 'red'
            elif days_left < ORDER_SOON_DAYS:
                status, cls = 'Order soon', 'amber'
            elif sellable <= p['reorder_level']:
                status, cls = 'Low stock', 'amber'
            else:
                continue
        elif sellable <= p['reorder_level']:
            need = needed(daily, position, cover, p['reorder_level'])   # same rule as the automatic PO
            status, cls = 'Low stock (no recent sales)', 'grey'
        else:
            continue

        qty = _round_up_to_pack(need, p.get('pack_factor') or 0)
        if qty <= 0:
            continue                                        # already covered by what is on order
        pf = p.get('pack_factor') or 0
        out.append(dict(
            id=pid, name=p['name'], unit=p.get('unit') or '', sellable=sellable, ordered=ordered,
            sold=int(sold_q), per_day=daily.quantize(Decimal('0.1')),
            days_left=None if days_left is None else days_left.quantize(Decimal('0.1')),
            qty=qty, pack_txt=(f"{qty // pf} {p['pack_name']}" if pf > 1 and qty % pf == 0 else ''),
            status=status, cls=cls, urgent=cls == 'red'))
    out.sort(key=lambda r: (r['days_left'] if r['days_left'] is not None else Decimal('9999'), r['name'].lower()))
    return out
