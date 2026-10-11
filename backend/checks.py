"""
Soft sanity checks for a purchase entry (typing mistakes). They only WARN, the user can still save.
Django-free so it can be tested on its own.

A line is a dict: product_id, name, qty, unit, base_qty, total (price paid incl. GST), selling_price, expire_date.
`history` maps product_id -> earlier purchase rows (see supplier_prices.rows_from_items), newest or oldest first.
"""
from datetime import timedelta
from decimal import Decimal

from . import supplier_prices as sp

PRICE_DEVIATION = Decimal('0.40')     # price per unit 40% above/below the recent average
QTY_TIMES = Decimal('10')             # quantity 10x the usual purchase quantity
SHORT_EXPIRY_DAYS = 90
RECENT = 5                            # how many earlier purchases count as "usual"


def _usual(rows):
    """(average rate per base unit, average base qty, count) of the latest purchases."""
    rows = sorted((r for r in rows if r.get('base_qty') and r.get('total') is not None and r.get('date')),
                  key=lambda r: (r['date'], r['id']))[-RECENT:]
    if not rows:
        return None, None, 0
    spent = sum((Decimal(r['total']) for r in rows), Decimal('0'))
    qty = sum((Decimal(r['base_qty']) for r in rows), Decimal('0'))
    return spent / qty, qty / len(rows), len(rows)


def purchase_warnings(lines, history, today):
    out = []
    seen = {}
    for ln in lines:
        name = ln.get('name') or 'Product'
        pid, base_qty, total = ln.get('product_id'), ln.get('base_qty') or 0, ln.get('total')
        seen[pid] = seen.get(pid, 0) + 1
        if seen[pid] == 2:
            out.append(f"{name}: added more than once in this invoice. Combine them if they are the same batch.")

        if total is not None and Decimal(total) == 0:
            out.append(f"{name}: the price is 0. Please check it.")
        elif total is not None and base_qty:
            rate = Decimal(total) / Decimal(base_qty)
            avg_rate, avg_qty, n = _usual(history.get(pid, []))
            if avg_rate and avg_rate > 0:
                diff = (rate - avg_rate) / avg_rate
                if diff >= PRICE_DEVIATION:
                    out.append(f"{name}: the price is {int(diff * 100)}% higher than usual "
                               f"({sp.money(rate)} vs about {sp.money(avg_rate)} per unit).")
                elif diff <= -PRICE_DEVIATION:
                    out.append(f"{name}: the price is {int(-diff * 100)}% lower than usual "
                               f"({sp.money(rate)} vs about {sp.money(avg_rate)} per unit).")
            if n >= 2 and avg_qty and Decimal(base_qty) >= avg_qty * QTY_TIMES:
                out.append(f"{name}: the quantity ({base_qty}) is much bigger than usual "
                           f"(about {int(avg_qty)}). Is an extra zero typed?")

        sell, qty = ln.get('selling_price'), ln.get('qty')
        if sell and qty and total is not None and Decimal(sell) < Decimal(total) / Decimal(qty):
            out.append(f"{name}: the selling price is below the purchase cost, so each sale will make a loss.")

        exp = ln.get('expire_date')
        if exp is None:
            out.append(f"{name}: no EXP date. Billing picks the batch that expires first, so please enter it.")
        elif exp < today:
            out.append(f"{name}: the EXP date is already over, so this stock cannot be sold.")
        elif exp <= today + timedelta(days=SHORT_EXPIRY_DAYS):
            out.append(f"{name}: expires within {SHORT_EXPIRY_DAYS} days ({exp:%d %b %Y}).")
    return out
