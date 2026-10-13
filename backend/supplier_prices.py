"""
Supplier-wise price comparison for products.

Everything is derived from the existing purchase entries (PurchaseItem), so no new table is needed.
Price used = total paid incl. GST / quantity in base units (e.g. per Tablet), so different pack sizes and
units are comparable. This module has no Django imports, so it can be tested on its own.

A "row" is a dict: id, product_id, product, base_unit, supplier_id, supplier, date, invoice, qty, unit,
base_qty, total.
"""
from decimal import Decimal, ROUND_HALF_UP

STALE_DAYS = 180                      # a supplier's price older than this is flagged as "old"
_FOUR = Decimal('0.0001')
_TWO = Decimal('0.01')
_ONE = Decimal('0.1')
_ZERO = Decimal('0')


def rows_from_items(qs):
    """PurchaseItem queryset -> plain row dicts (only rows that have a price and a quantity)."""
    qs = qs.filter(base_qty__gt=0, purchase_price__isnull=False)
    rows = []
    for r in qs.values('id', 'product_id', 'product__name', 'product__unit__name', 'purchase__supplier_id',
                       'purchase__supplier__company', 'purchase__invoice_date', 'purchase__invoice_number',
                       'qty', 'unit__name', 'base_qty', 'purchase_price'):
        rows.append(dict(
            id=r['id'], product_id=r['product_id'], product=r['product__name'],
            base_unit=r['product__unit__name'] or '', supplier_id=r['purchase__supplier_id'],
            supplier=r['purchase__supplier__company'], date=r['purchase__invoice_date'],
            invoice=r['purchase__invoice_number'], qty=r['qty'],
            unit=r['unit__name'] or r['product__unit__name'] or '', base_qty=r['base_qty'],
            total=r['purchase_price']))
    return rows


def unit_rate(total, base_qty):
    """Price per base unit (incl. GST), or None when it cannot be worked out."""
    if not base_qty or total is None:
        return None
    return (Decimal(total) / Decimal(base_qty)).quantize(_FOUR, ROUND_HALF_UP)


def pct(new, old):
    """Percentage change from old to new (1 decimal), or None."""
    if new is None or not old:
        return None
    return ((Decimal(new) - Decimal(old)) / Decimal(old) * 100).quantize(_ONE, ROUND_HALF_UP)


def money(x):
    """Rupee text. Cheap items (under Rs 10 per unit) keep 3 decimals so small differences stay visible."""
    if x is None:
        return '-'
    x = Decimal(x)
    return f"₹{x:,.2f}" if abs(x) >= 10 else f"₹{x:,.3f}"


def trend(change):
    """(text, badge colour) for a price change. A rise is bad (red), a fall is good (green)."""
    if change is None:
        return 'First', 'grey'
    if change > 0:
        return f"▲ {change}%", 'red'
    if change < 0:
        return f"▼ {abs(change)}%", 'green'
    return 'No change', 'grey'


def _usable(rows):
    return [r for r in rows if r.get('base_qty') and r.get('total') is not None and r.get('date')]


def build_history(rows):
    """Oldest-first purchase list with the rate of each purchase and the change vs the previous purchase
    from the same supplier."""
    rows = sorted(_usable(rows), key=lambda r: (r['date'], r['id']))
    last_rate = {}
    out = []
    for r in rows:
        rate = unit_rate(r['total'], r['base_qty'])
        last_rate_before = last_rate.get(r['supplier_id'])
        change = pct(rate, last_rate_before)
        last_rate[r['supplier_id']] = rate
        out.append(dict(r, rate=rate, change=change, prev_rate=last_rate_before))
    return out


def supplier_stats(history, today, pack_factor=None):
    """One entry per supplier, cheapest current price first."""
    groups = {}
    for h in history:                                   # history is oldest first
        g = groups.setdefault(h['supplier_id'], dict(
            supplier_id=h['supplier_id'], supplier=h['supplier'], purchases=0, base_qty=0, spent=_ZERO,
            rates=[], last=None, prev=None))
        g['purchases'] += 1
        g['base_qty'] += h['base_qty']
        g['spent'] += Decimal(h['total'])
        g['rates'].append(h['rate'])
        g['prev'], g['last'] = g['last'], h

    out = []
    for g in groups.values():
        last = g['last']
        change = pct(last['rate'], g['prev']['rate']) if g['prev'] else None
        t_txt, t_cls = trend(change)
        age = (today - last['date']).days
        out.append(dict(
            supplier_id=g['supplier_id'], supplier=g['supplier'], purchases=g['purchases'], total_qty=g['base_qty'],
            spent=g['spent'].quantize(_TWO), last_date=last['date'], last_invoice=last['invoice'],
            last_rate=last['rate'], min_rate=min(g['rates']), max_rate=max(g['rates']),
            avg_rate=(g['spent'] / g['base_qty']).quantize(_FOUR, ROUND_HALF_UP),
            trend_txt=t_txt, trend_cls=t_cls, age_days=age, stale=age > STALE_DAYS))
    out.sort(key=lambda s: (s['last_rate'], s['supplier'].lower()))

    # An old price (not bought for a long time) should not be crowned "cheapest" while others are current.
    current = [s for s in out if not s['stale']] or out
    best = current[0]['last_rate'] if current else None
    for s in out:
        s['current'] = s in current
        s['is_best'] = len(current) > 1 and s['current'] and s['last_rate'] == best
        diff = pct(s['last_rate'], best) if best else None
        s['vs_best'] = diff if diff is not None else Decimal('0.0')
        for key in ('last_rate', 'min_rate', 'max_rate', 'avg_rate'):
            s[key + '_txt'] = money(s[key])
            s[key + '_pack_txt'] = money((s[key] * pack_factor).quantize(_TWO, ROUND_HALF_UP)) if pack_factor else ''
    return out


def summary(suppliers):
    """Headline numbers: cheapest and costliest current supplier and the possible saving."""
    if not suppliers:
        return dict(count=0, current_count=0, best=None, worst=None, saving_pct=None)
    current = [s for s in suppliers if s.get('current', True)] or suppliers
    best, worst = current[0], current[-1]
    saving = None
    if len(current) > 1 and worst['last_rate'] > 0:
        saving = ((worst['last_rate'] - best['last_rate']) / worst['last_rate'] * 100).quantize(_ONE, ROUND_HALF_UP)
    return dict(count=len(suppliers), current_count=len(current), best=best, worst=worst, saving_pct=saving)


def compare(rows, today, pack_factor=None, history_limit=100):
    """Everything the product page needs."""
    history = build_history(rows)
    suppliers = supplier_stats(history, today, pack_factor)
    newest_first = list(reversed(history))
    for h in newest_first:
        h['rate_txt'] = money(h['rate'])
        h['pack_rate_txt'] = money((h['rate'] * pack_factor).quantize(_TWO, ROUND_HALF_UP)) if pack_factor else ''
        h['trend_txt'], h['trend_cls'] = trend(h['change'])
    return dict(suppliers=suppliers, summary=summary(suppliers), history=newest_first[:history_limit],
                history_total=len(history))


def overview(rows, today):
    """One line per product for the main page: how many suppliers, cheapest vs costliest."""
    by_product = {}
    for r in _usable(rows):
        by_product.setdefault(r['product_id'], []).append(r)
    out = []
    for pid, items in by_product.items():
        history = build_history(items)
        sup = supplier_stats(history, today)
        sm = summary(sup)
        best, worst = sm['best'], sm['worst']
        out.append(dict(
            product_id=pid, product=items[0]['product'], base_unit=items[0].get('base_unit') or '',
            suppliers_n=sm['count'], purchases=len(history), last_date=history[-1]['date'],
            best_supplier=best['supplier'], best_rate=best['last_rate'], best_txt=money(best['last_rate']),
            worst_supplier=worst['supplier'], worst_rate=worst['last_rate'], worst_txt=money(worst['last_rate']),
            saving_pct=sm['saving_pct']))
    out.sort(key=lambda p: (-p['suppliers_n'], -(p['saving_pct'] or _ZERO), p['product'].lower()))
    return out


def cheapest_supplier_id(rows, today):
    """Id of the supplier with the lowest current price for these rows (all rows of ONE product), or None."""
    history = build_history(rows)
    suppliers = supplier_stats(history, today)
    current = [x for x in suppliers if x['current']] or suppliers
    return current[0]['supplier_id'] if current else None


def pick_supplier(rows, default_id, today):
    """Supplier for an automatic order (rows = all purchase rows of ONE product): the supplier whose latest price is the
    lowest (a price not bought for a long time does not count while others are current), else the product's default
    supplier, else the supplier of the latest purchase. None when nothing is known."""
    sid = cheapest_supplier_id(rows, today) if rows else None
    if not sid:
        sid = default_id
    if not sid and rows:
        sid = max(rows, key=lambda r: (r['date'], r['id']))['supplier_id']
    return sid or None


def price_jumps(rows, today, min_pct=Decimal('10'), within_days=60, limit=8):
    """Recent price increases: a supplier's latest price is at least min_pct above its previous price and was
    bought within the last `within_days` days. Rows may cover many products. Biggest rise first.
    If a different current supplier is cheaper, it is returned as the alternative."""
    by_product = {}
    for r in _usable(rows):
        by_product.setdefault(r['product_id'], []).append(r)
    out = []
    for items in by_product.values():
        history = build_history(items)
        newest = {}
        for h in history:                       # oldest first, so the last one per supplier wins
            newest[h['supplier_id']] = h
        suppliers = None
        for h in newest.values():
            if h['change'] is None or h['change'] < min_pct or (today - h['date']).days > within_days:
                continue
            if suppliers is None:
                suppliers = supplier_stats(history, today)
            alt = next((x for x in suppliers if x['current'] and x['supplier_id'] != h['supplier_id']
                        and x['last_rate'] < h['rate']), None)
            out.append(dict(
                product_id=h['product_id'], product=h['product'], supplier=h['supplier'], date=h['date'],
                old_txt=money(h['prev_rate']), new_txt=money(h['rate']), change=h['change'],
                base_unit=h.get('base_unit') or '',
                alt_supplier=alt['supplier'] if alt else '', alt_txt=alt['last_rate_txt'] if alt else ''))
    out.sort(key=lambda x: (-x['change'], x['product'].lower()))
    return out[:limit]
