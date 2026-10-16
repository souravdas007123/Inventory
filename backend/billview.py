"""Data for the Billing list's 👁 Preview window (embedded in the list page, so the window opens instantly)."""
from decimal import ROUND_HALF_UP, Decimal

ZERO, TWO = Decimal('0'), Decimal('0.01')


def money(x):
    return f"₹{(x or 0):,.2f}"


def preview_data(bill, items, batch_map, invoice_url):
    """`items` = the bill's InvoiceItem rows (with product / unit loaded), `batch_map` = {batch id: Batch}."""
    rows, disc_total = [], ZERO
    for n, it in enumerate(items, 1):
        batches = []
        for k in (it.batch_history or {}):
            b = batch_map.get(int(k))
            if b:
                batches.append(f"{b.batch_number} / {b.expire_date.strftime('%m-%y') if b.expire_date else '-'}")
        gross = (it.rate or ZERO) * (it.qty or 0)
        disc = max((gross - (it.taxable_value or ZERO)).quantize(TWO), ZERO)
        disc_total += disc
        rows.append(dict(n=n, name=(it.product.name if it.product_id else '') or '-',
                         comp=(it.product.composition if it.product_id else '') or '', batch=', '.join(batches) or '-',
                         qty=f"{it.qty} {it.unit or ''}".strip(), rate=money(it.rate), disc=money(disc) if disc > 0 else '-',
                         gst=f"{format(Decimal(it.gst_rate or 0).normalize(), 'f')}%", total=money(it.total)))

    def s(field):
        return sum((getattr(i, field) or ZERO for i in items), ZERO)

    total = s('total')
    grand = total.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    credit = bill.payment_mode == 'CREDIT'
    paid = bill.amount_paid or ZERO
    return dict(
        ok=True, invoice=bill.display_number, date=bill.date.strftime('%d %b %Y') if bill.date else '-',
        customer=bill.customer_name or '-', phone=(bill.customer.phone if bill.customer_id else '') or '',
        cancelled=bill.status != 'FINAL', payment=bill.get_payment_mode_display(), credit=credit,
        doctor=bill.doctor_name or '', patient=bill.patient_name or '', count=len(rows), items=rows,
        totals=dict(taxable=money(s('taxable_value')), gst=money(s('cgst') + s('sgst') + s('igst')),
                    discount=money(disc_total) if disc_total > 0 else '', round_off=money(grand - total) if grand != total else '',
                    grand=money(grand), paid=money(paid) if credit else '', due=money(max(grand - paid, ZERO)) if credit else ''),
        invoice_url=invoice_url)


def purchase_preview_data(purchase, items, today, edit_url='', return_url='', warn_days=90):
    """Preview of a supplier purchase invoice: every item with batch / expiry / qty / price, and the totals.
    `items` = PurchaseItem rows (product / unit loaded). An item that expires within `warn_days` (or already did) is flagged."""
    from datetime import timedelta
    limit = today + timedelta(days=warn_days)
    rows, short = [], 0
    for n, it in enumerate(items, 1):
        exp = it.expire_date
        flag = '' if not exp else ('expired' if exp < today else ('soon' if exp <= limit else ''))
        short += bool(flag)
        per = (it.purchase_price / it.qty).quantize(TWO) if it.qty and it.purchase_price is not None else None
        rows.append(dict(n=n, name=(it.product.name if it.product_id else '') or '-',
                         comp=(it.composition or (it.product.composition if it.product_id else '')) or '',
                         batch=it.batch or '-', exp=exp.strftime('%m-%y') if exp else '-', flag=flag,
                         qty=f"{it.qty} {it.unit or ''}".strip(), rate=money(per) if per is not None else '-',
                         mrp=money(it.selling_price) if it.selling_price else '-',
                         gst=f"{it.gst_rate or 0}%", total=money(it.purchase_price)))

    def s(field):
        return sum((getattr(i, field) or ZERO for i in items), ZERO)

    return dict(
        ok=True, invoice=purchase.invoice_number, date=purchase.invoice_date.strftime('%d %b %Y') if purchase.invoice_date else '-',
        supplier=purchase.supplier.company, count=len(rows), short=short, items=rows,
        totals=dict(taxable=money(s('tax')), gst=money(s('gst')), total=money(s('purchase_price'))),
        edit_url=edit_url, return_url=return_url)


def pack_text(qty, convs, base):
    """`qty` base units as packs, e.g. 33 -> '2 Strip 3 Tablet'. `convs` = [(unit name, factor)] (factor > 1)."""
    parts, left = [], qty or 0
    for name, factor in sorted(convs, key=lambda c: c[1], reverse=True):
        n, left = divmod(left, factor)
        if n:
            parts.append(f"{n} {name}")
    if left or not parts:
        parts.append(f"{left} {base}")
    return ' '.join(parts)


def product_preview_data(product, today, edit_url='', prices_url='', warn_days=90):
    """Preview of a product: details, how its units convert, stock split in batches (first to expire first)."""
    from datetime import timedelta
    p, limit = product, today + timedelta(days=warn_days)
    base = p.unit.name if p.unit_id else 'unit'
    convs = [(c.unit.name, c.factor) for c in p.unit_conversions.all() if c.factor and c.factor > 1]
    batches = sorted((b for b in p.batches.all() if b.qty and b.qty > 0),
                     key=lambda b: (b.expire_date is None, b.expire_date or today, b.id))
    rows, sellable, expired = [], 0, 0
    for b in batches:
        exp = b.expire_date
        flag = '' if not exp else ('expired' if exp < today else ('soon' if exp <= limit else ''))
        if flag == 'expired':
            expired += b.qty
        else:
            sellable += b.qty
        place = ' / '.join(x for x in (b.location, b.rack, b.row) if x)
        rows.append(dict(batch=b.batch_number or '-', exp=exp.strftime('%m-%y') if exp else '-', flag=flag,
                         qty=pack_text(b.qty, convs, base), supplier=(b.supplier.company if b.supplier_id else '') or '-',
                         place=place or '-'))
    status = ('Out of stock', 'red') if p.stock_qty == 0 else (('Low stock', 'amber') if p.stock_qty <= p.reorder_level else ('In stock', 'green'))
    facts = [('Brand', p.brand), ('Category', p.category), ('HSN', p.hsn_code), ('GST', f"{format(Decimal(p.gst_rate or 0).normalize(), 'f')}%"),
             ('MRP', f"{money(p.mrp)} per {base}" if p.mrp is not None else None), ('SKU', p.sku), ('Barcode', p.barcode),
             ('Supplier', p.default_supplier.company if p.default_supplier_id else None),
             ('Place', ' / '.join(x for x in (p.location, p.rack, p.row) if x) or None),
             ('Reorder at', pack_text(p.reorder_level, convs, base))]
    return dict(
        ok=True, name=p.name or '-', comp=p.composition or '', schedule=p.schedule if p.schedule and p.schedule != 'NONE' else '',
        active=p.is_active is not False, status=status[0], status_kind=status[1],
        stock=pack_text(p.stock_qty, convs, base), base=f"{p.stock_qty} {base}" if convs else '',
        sellable=pack_text(sellable, convs, base), expired=pack_text(expired, convs, base) if expired else '',
        units=[f"1 {n} = {f} {base}" for n, f in sorted(convs, key=lambda c: c[1])],
        facts=[[k, str(v)] for k, v in facts if v not in (None, '')], batches=rows, count=len(rows),
        edit_url=edit_url, prices_url=prices_url)
