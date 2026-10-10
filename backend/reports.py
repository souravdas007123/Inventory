"""
Report calculations: ek hi jagah, taaki web page, admin aur Excel export sab same number dikhayein.

- profit_and_loss      : P&L (returns aur write-off ke saath)
- gst_report           : GSTR-1 (B2B/B2C/Credit notes), HSN summary, GSTR-3B summary
- drug_register        : Schedule H/H1/X bikri register
- dashboard_charts     : daily sales, top selling, dead stock
- gst_workbook / register_workbook : Excel (.xlsx) export
"""
import io
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import (
    Batch, Bill, Expense, InvoiceItem, Product, PurchaseItem, PurchaseReturnItem,
    Sale, SalesReturnItem, StockWriteOff,
)

ZERO = Decimal('0')
TWO = Decimal('0.01')


def dec(x):
    return Decimal(str(x or 0))


def q2(x):
    return Decimal(x).quantize(TWO)


# ======================================================
# PROFIT & LOSS
# ======================================================

def profit_and_loss(month, year):
    """
    Income    = Net Sales ki taxable value (return ke baad; GST income nahi hai)
    COGS      = jo maal bika uski purchase cost (batch_history se) - wapas aaye maal ki cost
    Write-off = expired/damaged maal ki purchase cost (loss)
    Purchases = sirf information ke liye (is mahine kharida hua taxable maal, return ke baad)
    """
    # Sale rows me return ke liye negative rows hoti hain, to ye sum net hai
    sales = dec(Sale.objects.filter(date__month=month, date__year=year)
                .aggregate(s=Sum('taxable_value'))['s'])

    histories = list(InvoiceItem.objects.filter(sale__date__month=month, sale__date__year=year)
                     .values_list('batch_history', flat=True))
    batch_ids = {int(k) for h in histories for k in (h or {})}
    batch_rate = dict(Batch.objects.filter(pk__in=batch_ids, purchase_item__isnull=False)
                      .values_list('pk', 'purchase_item__rate'))
    cogs = ZERO
    for history in histories:
        for batch_id, qty in (history or {}).items():
            cogs += dec(batch_rate.get(int(batch_id))) * qty
    cogs -= dec(SalesReturnItem.objects.filter(
        sales_return__date__month=month, sales_return__date__year=year
    ).aggregate(s=Sum('cost'))['s'])

    purchases = dec(PurchaseItem.objects.filter(
        purchase__invoice_date__month=month, purchase__invoice_date__year=year
    ).aggregate(s=Sum('tax'))['s'])
    purchases -= dec(PurchaseReturnItem.objects.filter(
        purchase_return__date__month=month, purchase_return__date__year=year
    ).aggregate(s=Sum('taxable_value'))['s'])

    expenses = dec(Expense.objects.filter(date__month=month, date__year=year)
                   .aggregate(s=Sum('amount'))['s'])
    writeoffs = dec(StockWriteOff.objects.filter(date__month=month, date__year=year)
                    .aggregate(s=Sum('cost'))['s'])

    gross = sales - cogs
    return dict(sales=sales, cogs=cogs, gross=gross, purchases=purchases,
                expenses=expenses, writeoffs=writeoffs, net=gross - expenses - writeoffs)


# ======================================================
# GST REPORTS
# ======================================================

def _tax_row():
    return dict(taxable=ZERO, cgst=ZERO, sgst=ZERO, igst=ZERO, total=ZERO)


def _add(row, taxable, cgst, sgst, igst, total, sign=1):
    row['taxable'] += sign * dec(taxable)
    row['cgst'] += sign * dec(cgst)
    row['sgst'] += sign * dec(sgst)
    row['igst'] += sign * dec(igst)
    row['total'] += sign * dec(total)


def gst_report(month, year):
    """
    Mahine ka GSTR-1 / GSTR-3B jaisa summary. Sirf FINAL bills aur us mahine ke credit notes ginte hain.
    Note: ye accounting summary hai, return file karne se pehle apne CA se verify karein.
    """
    items = (InvoiceItem.objects
             .filter(bill__status='FINAL', bill__date__month=month, bill__date__year=year)
             .select_related('bill', 'bill__customer', 'product'))

    b2b = OrderedDict()      # bill id -> row
    b2c = OrderedDict()      # gst rate -> row
    hsn = OrderedDict()      # (hsn, rate) -> row
    outward = _tax_row()

    for it in sorted(items, key=lambda i: (i.bill.date, i.bill_id, i.id)):
        bill = it.bill
        gstin = bill.customer.gstin if bill.customer_id and bill.customer.gstin else ''
        if gstin:
            row = b2b.setdefault(bill.pk, dict(
                invoice=bill.display_number, date=bill.date, customer=bill.customer_name,
                gstin=gstin, **_tax_row()))
        else:
            row = b2c.setdefault(it.gst_rate, dict(rate=it.gst_rate, **_tax_row()))
        _add(row, it.taxable_value, it.cgst, it.sgst, it.igst, it.total)

        key = ((it.product.hsn_code if it.product_id and it.product.hsn_code else '-'), it.gst_rate)
        hrow = hsn.setdefault(key, dict(hsn=key[0], rate=key[1], qty=0, **_tax_row()))
        hrow['qty'] += it.base_qty
        _add(hrow, it.taxable_value, it.cgst, it.sgst, it.igst, it.total)
        _add(outward, it.taxable_value, it.cgst, it.sgst, it.igst, it.total)

    # ---- Credit notes (sales return) ----
    credit_notes = OrderedDict()
    returns = (SalesReturnItem.objects
               .filter(sales_return__date__month=month, sales_return__date__year=year)
               .select_related('sales_return', 'sales_return__bill', 'sales_return__bill__customer',
                               'invoice_item', 'invoice_item__product')
               .order_by('sales_return__date', 'sales_return_id', 'id'))
    for r in returns:
        sr = r.sales_return
        bill = sr.bill
        gstin = bill.customer.gstin if bill.customer_id and bill.customer.gstin else ''
        row = credit_notes.setdefault(sr.pk, dict(
            note=sr.note_number, date=sr.date, invoice=bill.display_number,
            customer=bill.customer_name, gstin=gstin, **_tax_row()))
        _add(row, r.taxable_value, r.cgst, r.sgst, r.igst, r.total)
        _add(outward, r.taxable_value, r.cgst, r.sgst, r.igst, r.total, sign=-1)
        ii = r.invoice_item
        key = ((ii.product.hsn_code if ii.product_id and ii.product.hsn_code else '-'), ii.gst_rate)
        hrow = hsn.setdefault(key, dict(hsn=key[0], rate=key[1], qty=0, **_tax_row()))
        hrow['qty'] -= r.base_qty
        _add(hrow, r.taxable_value, r.cgst, r.sgst, r.igst, r.total, sign=-1)
        # B2C return: rate wise net kam karo
        if not gstin:
            crow = b2c.setdefault(ii.gst_rate, dict(rate=ii.gst_rate, **_tax_row()))
            _add(crow, r.taxable_value, r.cgst, r.sgst, r.igst, r.total, sign=-1)

    for row in credit_notes.values():
        row['gst'] = row['cgst'] + row['sgst'] + row['igst']

    # ---- Documents issued ----
    bills = Bill.objects.filter(date__month=month, date__year=year).order_by('invoice_number')
    numbers = [b.invoice_number for b in bills if b.invoice_number]
    docs = dict(
        invoices=len(numbers), first=numbers[0] if numbers else '-', last=numbers[-1] if numbers else '-',
        cancelled=sum(1 for b in bills if b.status == 'CANCELLED'), credit_notes=len(credit_notes),
    )

    # ---- ITC (purchases) ----
    pur = PurchaseItem.objects.filter(purchase__invoice_date__month=month, purchase__invoice_date__year=year)
    itc = dict(taxable=dec(pur.aggregate(s=Sum('tax'))['s']), cgst=dec(pur.aggregate(s=Sum('cgst'))['s']),
               sgst=dec(pur.aggregate(s=Sum('sgst'))['s']), igst=ZERO)
    for r in PurchaseReturnItem.objects.filter(purchase_return__date__month=month,
                                               purchase_return__date__year=year):
        half = q2(r.gst / 2)
        itc['taxable'] -= r.taxable_value
        itc['cgst'] -= half
        itc['sgst'] -= (r.gst - half)

    net = dict(cgst=outward['cgst'] - itc['cgst'], sgst=outward['sgst'] - itc['sgst'],
               igst=outward['igst'] - itc['igst'])
    net['total'] = net['cgst'] + net['sgst'] + net['igst']

    return dict(
        month=month, year=year,
        b2b=list(b2b.values()), b2c=sorted(b2c.values(), key=lambda r: r['rate']),
        credit_notes=list(credit_notes.values()),
        hsn=sorted(hsn.values(), key=lambda r: (str(r['hsn']), r['rate'])),
        docs=docs, outward=outward, itc=itc, net=net,
    )


def _sheet(wb, title, headers, rows, first=False):
    from openpyxl.styles import Font
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    for idx, header in enumerate(headers, 1):
        width = max([len(str(header))] + [len(str(r[idx - 1])) for r in rows if len(r) >= idx] or [10])
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = min(max(width + 2, 10), 40)
    return ws


def _n(x):
    return float(x)


def gst_workbook(rep):
    """GST report ko Excel (.xlsx) bytes me badalta hai."""
    from openpyxl import Workbook
    wb = Workbook()
    tax_h = ['Taxable', 'CGST', 'SGST', 'IGST', 'Total']

    def tax_cells(r):
        return [_n(r['taxable']), _n(r['cgst']), _n(r['sgst']), _n(r['igst']), _n(r['total'])]

    _sheet(wb, 'GSTR-3B Summary', ['Particular', 'Taxable', 'CGST', 'SGST', 'IGST'], [
        ['Outward supplies (net of credit notes)', _n(rep['outward']['taxable']), _n(rep['outward']['cgst']),
         _n(rep['outward']['sgst']), _n(rep['outward']['igst'])],
        ['ITC from purchases (net of debit notes)', _n(rep['itc']['taxable']), _n(rep['itc']['cgst']),
         _n(rep['itc']['sgst']), _n(rep['itc']['igst'])],
        ['Net GST payable', '', _n(rep['net']['cgst']), _n(rep['net']['sgst']), _n(rep['net']['igst'])],
    ], first=True)
    _sheet(wb, 'B2B Invoices', ['Invoice', 'Date', 'Customer', 'GSTIN'] + tax_h,
           [[r['invoice'], r['date'].strftime('%d-%m-%Y'), r['customer'], r['gstin']] + tax_cells(r)
            for r in rep['b2b']])
    _sheet(wb, 'B2C (rate wise)', ['GST %'] + tax_h, [[r['rate']] + tax_cells(r) for r in rep['b2c']])
    _sheet(wb, 'Credit Notes', ['Note', 'Date', 'Against Invoice', 'Customer', 'GSTIN'] + tax_h,
           [[r['note'], r['date'].strftime('%d-%m-%Y'), r['invoice'], r['customer'], r['gstin']] + tax_cells(r)
            for r in rep['credit_notes']])
    _sheet(wb, 'HSN Summary', ['HSN', 'GST %', 'Qty (base unit)'] + tax_h,
           [[r['hsn'], r['rate'], r['qty']] + tax_cells(r) for r in rep['hsn']])
    d = rep['docs']
    _sheet(wb, 'Documents', ['Invoices issued', 'First', 'Last', 'Cancelled', 'Credit notes'],
           [[d['invoices'], d['first'], d['last'], d['cancelled'], d['credit_notes']]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ======================================================
# DRUG REGISTER (Schedule H / H1 / X)
# ======================================================

def drug_register(month, year, schedules=('H1', 'X')):
    items = list(InvoiceItem.objects
                 .filter(bill__status='FINAL', bill__date__month=month, bill__date__year=year,
                         product__schedule__in=schedules)
                 .select_related('bill', 'product', 'unit')
                 .order_by('bill__date', 'bill_id', 'id'))
    batch_map = Batch.objects.in_bulk({int(k) for it in items for k in (it.batch_history or {})})
    rows = []
    for it in items:
        batches = []
        for key in (it.batch_history or {}):
            b = batch_map.get(int(key))
            if b:
                batches.append(f"{b.batch_number}" + (f" / {b.expire_date:%m-%y}" if b.expire_date else ''))
        bill = it.bill
        rows.append(dict(
            date=bill.date, invoice=bill.display_number, patient=bill.patient_name,
            address=bill.patient_address, doctor=bill.doctor_name, reg_no=bill.doctor_reg_no,
            product=it.product.name or str(it.product), schedule=it.product.schedule,
            batch=', '.join(batches), qty=f"{it.qty} {it.unit or ''}".strip(),
        ))
    return rows


def register_workbook(rows):
    from openpyxl import Workbook
    wb = Workbook()
    headers = ['Date', 'Invoice', 'Patient', 'Patient Address', 'Doctor', 'Doctor Reg. No.',
               'Medicine', 'Schedule', 'Batch / Exp', 'Qty']
    _sheet(wb, 'Drug Register', headers,
           [[r['date'].strftime('%d-%m-%Y'), r['invoice'], r['patient'], r['address'], r['doctor'],
             r['reg_no'], r['product'], r['schedule'], r['batch'], r['qty']] for r in rows], first=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ======================================================
# DASHBOARD CHARTS
# ======================================================

def dashboard_charts(today=None, days=14):
    """Daily sales (last N din), top selling (30 din), dead stock (90 din se bikri nahi)."""
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    daily = {d: dec(s) for d, s in Sale.objects.filter(date__gte=start, date__lte=today)
             .values_list('date').annotate(s=Sum('total')).order_by()}
    series = []
    for i in range(days):
        day = start + timedelta(days=i)
        series.append((day, daily.get(day, ZERO)))
    peak = max((v for _, v in series), default=ZERO)
    bars = [dict(label=day.strftime('%d %b'), value=val,
                 pct=int(round(float(val / peak) * 100)) if peak > 0 and val > 0 else 0)
            for day, val in series]

    recent = InvoiceItem.objects.filter(bill__status='FINAL', bill__date__gte=today - timedelta(days=29))
    top = list(recent.filter(product__isnull=False).values('product_id', 'product__name')
               .annotate(amount=Sum('taxable_value'), qty=Sum('base_qty')).order_by('-amount')[:5])
    top_max = max((dec(t['amount']) for t in top), default=ZERO)
    top_rows = [dict(name=t['product__name'] or '-', amount=dec(t['amount']), qty=t['qty'],
                     pct=int(round(float(dec(t['amount']) / top_max) * 100)) if top_max > 0 else 0)
                for t in top]

    sold_ids = (InvoiceItem.objects.filter(bill__status='FINAL', bill__date__gte=today - timedelta(days=90))
                .filter(product__isnull=False).values_list('product_id', flat=True).distinct())
    dead = [(p.name or str(p), p.stock_display()) for p in
            Product.objects.exclude(is_active=False).filter(stock_qty__gt=0).exclude(pk__in=sold_ids)
            .prefetch_related('unit_conversions__unit').select_related('unit').order_by('-stock_qty')[:8]]
    return dict(bars=bars, total_14=sum((v for _, v in series), ZERO), top_rows=top_rows, dead_stock=dead)
