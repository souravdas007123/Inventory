"""
Expiring / expired batches that can go back to the supplier, grouped supplier -> purchase invoice.

Django-free so it can be tested on its own. A row is a dict: batch_id, product, batch_number, expire_date, qty (base units
left in the batch), factor (base units in one purchase unit), unit (purchase unit name), supplier_id, supplier, purchase_id,
invoice, invoice_date, total (price of the whole purchase line), line_qty (qty of the purchase line, in purchase units).

A return is booked in purchase units (Strip, Box ...), so only whole units can go back: 25 tablets = 2 strips of 10 + 5 loose.
"""
from decimal import Decimal

TWO = Decimal('0.01')
DAY_CHOICES = (30, 60, 90, 180)
DEFAULT_DAYS = 90


def returnable_units(qty, factor):
    """Whole purchase units that can be returned from `qty` base units; (units, loose base units left over)."""
    factor = max(int(factor or 1), 1)
    qty = max(int(qty or 0), 0)
    return qty // factor, qty % factor


def plan(rows, today, days=DEFAULT_DAYS):
    """Returns [{supplier_id, supplier, total, expired_total, count, invoices: [{purchase_id, invoice, invoice_date, items}]}]
    for batches that are expired or expire within `days`; items carry units / loose / value / status / left."""
    from datetime import timedelta
    limit = today + timedelta(days=days)
    by_supplier = {}
    for r in rows:
        exp = r.get('expire_date')
        if not exp or exp > limit or (r.get('qty') or 0) <= 0:
            continue
        units, loose = returnable_units(r['qty'], r.get('factor'))
        line_qty = r.get('line_qty') or 0
        value = (Decimal(r.get('total') or 0) * units / line_qty).quantize(TWO) if line_qty and units else Decimal('0.00')
        expired = exp < today
        item = dict(batch_id=r['batch_id'], product=r['product'], batch_number=r.get('batch_number') or '-',
                    expire_date=exp, left=(exp - today).days, ago=(today - exp).days if expired else 0, expired=expired, units=units, loose=loose,
                    unit=r.get('unit') or '', value=value, returnable=units > 0)
        sup = by_supplier.setdefault(r['supplier_id'], dict(supplier_id=r['supplier_id'], supplier=r['supplier'],
                                                           phone=r.get('phone') or '', invoices={}))
        inv = sup['invoices'].setdefault(r['purchase_id'], dict(purchase_id=r['purchase_id'], invoice=r['invoice'],
                                                                invoice_date=r.get('invoice_date'), items=[]))
        inv['items'].append(item)
    out = []
    for sup in by_supplier.values():
        invoices = sorted(sup['invoices'].values(), key=lambda i: min(x['expire_date'] for x in i['items']))
        for inv in invoices:
            inv['items'].sort(key=lambda x: (x['expire_date'], x['product']))
        items = [x for inv in invoices for x in inv['items']]
        out.append(dict(supplier_id=sup['supplier_id'], supplier=sup['supplier'], phone=sup['phone'], invoices=invoices,
                        count=len(items), total=sum((x['value'] for x in items if x['returnable']), Decimal('0.00')),
                        expired_total=sum((x['value'] for x in items if x['returnable'] and x['expired']), Decimal('0.00')),
                        expired_count=sum(1 for x in items if x['expired']),
                        first=min(x['expire_date'] for x in items)))
    out.sort(key=lambda s: (s['first'], s['supplier']))
    return out


def whatsapp_text(shop, supplier, items):
    """Message to the supplier listing what we would like to return."""
    lines = [f"Hello {supplier}, this is {shop or 'MedSync'}.",
             "These medicines are expired / about to expire and we would like to return them against a credit note:"]
    for x in items[:25]:
        when = 'EXPIRED' if x['expired'] else 'exp'
        lines.append(f"- {x['product']} | batch {x['batch_number']} | {when} {x['expire_date']:%b %Y} | {x['units']} {x['unit']}".rstrip())
    if len(items) > 25:
        lines.append(f"... and {len(items) - 25} more")
    lines.append("Please let us know when we can send them or arrange a pickup. Thank you!")
    return '\n'.join(lines)
