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
