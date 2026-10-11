"""
Reorder suggestions from real sales speed. Django-free so it can be tested on its own.

For every product: units sold per day over the last `days` days -> how many days the sellable stock will last ->
how much to order so that about `cover` days of stock are available. Stock already on a pending/sent purchase order
counts as available. Quantities are in the product's base unit and are rounded up to whole packs when the
product has a pack (e.g. Strip).

A product is a dict: id, name, unit, stock_qty, reorder_level, pack_name, pack_factor.
"""
from decimal import Decimal, ROUND_CEILING

ORDER_NOW_DAYS = 7        # stock lasts less than this many days -> order now
ORDER_SOON_DAYS = 15      # ... less than this -> order soon


def _ceil(x):
    return int(Decimal(x).to_integral_value(rounding=ROUND_CEILING))


def _round_up_to_pack(qty, factor):
    if factor and factor > 1 and qty > 0:
        return _ceil(Decimal(qty) / factor) * factor
    return qty


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
            need = _ceil(daily * cover) - position
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
            need = p['reorder_level'] * 2 - position       # same rule as the automatic PO when nothing sold lately
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
