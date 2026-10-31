"""Billing details of a shop, and the tax invoices: the shop owner's own invoice page, and the platform owner's invoice list
(with a CSV for the GST return)."""
import csv
import re
from datetime import date

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from backend import access
from backend.dataexport import safe

from . import gst
from .models import Invoice, Shop
from .payshop import _shop
from .platform import platform_admin_required
from .taxdata import INDIA_STATES


@access.owner_required
@require_POST
def billing_save(request):
    """The 'details for your invoice' form on the Plan page. Open for a locked shop too (it is part of paying)."""
    shop = _shop(request)
    values, errors = gst.clean_billing(request.POST)
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect('plan')
    Shop.objects.filter(pk=shop.pk).update(**values)
    messages.success(request, 'Your invoice details are saved.')
    return redirect('plan')


def _context(invoice, back, back_label):
    """Everything the invoice page shows, in plain words."""
    export = invoice.kind == 'export'
    place = (gst.country_name(invoice.buyer_country) if export or invoice.buyer_country != 'IN'
             else (f'{INDIA_STATES.get(invoice.buyer_state, "")} ({invoice.buyer_state})' if invoice.buyer_state else 'India'))
    note = ''
    if export:
        note = ('Supply of services for export, zero-rated: no IGST is charged'
                + (f' (under Letter of Undertaking {invoice.seller_lut}).' if invoice.seller_lut else '.'))
    elif invoice.kind == 'none':
        note = 'The seller is not registered under GST, so no GST is charged.'
    state_code = gst.state_of(invoice.seller_gstin)
    return dict(invoice=invoice, title='Invoice' if invoice.kind == 'none' else 'Tax Invoice', place=place, note=note,
                seller_state=f'{INDIA_STATES.get(state_code, "")} ({state_code})' if state_code else '',
                buyer_country_name=gst.country_name(invoice.buyer_country), back=back, back_label=back_label,
                half_rate=invoice.rate / 2, rows=gst.rows_for(dict(
                    kind=invoice.kind, rate=invoice.rate, taxable=invoice.taxable, cgst=invoice.cgst, sgst=invoice.sgst,
                    igst=invoice.igst, total=invoice.total), 'Total (paid)'))


@access.owner_required
def invoice_view(request, pk):
    shop = _shop(request)
    invoice = get_object_or_404(Invoice, pk=pk, shop=shop)
    return render(request, 'app/tax_invoice.html', _context(invoice, '/plan/', 'Back to Plan and payments'))


@platform_admin_required
def invoice_open(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(request, 'app/tax_invoice.html', _context(invoice, '/superadmin/invoices/', 'Back to invoices'))


def _month(request):
    """The month asked for (?month=2026-10), else the current one, as (first day, first day of the next month)."""
    m = re.fullmatch(r'(\d{4})-(\d{2})', request.GET.get('month') or '')
    today = timezone.localdate()
    year, month = (int(m.group(1)), int(m.group(2))) if m and 1 <= int(m.group(2)) <= 12 and 2000 <= int(m.group(1)) <= 2100 else (today.year, today.month)
    first = date(year, month, 1)
    return first, date(year + (month == 12), month % 12 + 1, 1)


@platform_admin_required
def invoices(request):
    first, nxt = _month(request)
    rows = Invoice.objects.filter(date__gte=first, date__lt=nxt).select_related('shop').order_by('date', 'id')
    totals = {k: sum(getattr(r, k) for r in rows) for k in ('taxable', 'cgst', 'sgst', 'igst', 'total')}
    return render(request, 'app/platform_invoices.html', dict(rows=rows, totals=totals, month=f'{first:%Y-%m}', month_label=f'{first:%B %Y}'))


@platform_admin_required
def invoices_csv(request):
    """The month's invoices for the GST return (one line per invoice)."""
    first, nxt = _month(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="invoices-{first:%Y-%m}.csv"'
    response.write('﻿')                                           # so that Excel reads the file as UTF-8
    w = csv.writer(response)
    w.writerow(['Invoice no', 'Date', 'Customer', 'Customer GSTIN / tax no', 'Country', 'Place of supply (state code)', 'Kind', 'Taxable value',
                'CGST', 'SGST', 'IGST', 'Total', 'Plan', 'From', 'To'])
    for r in Invoice.objects.filter(date__gte=first, date__lt=nxt).order_by('date', 'id'):
        # the customer typed the name and tax number: a cell that starts with = + - @ must never run as an Excel formula
        w.writerow([r.number, r.date.isoformat(), safe(r.buyer_name), safe(r.buyer_tax_id), gst.country_name(r.buyer_country), r.buyer_state,
                    r.get_kind_display(), r.taxable, r.cgst, r.sgst, r.igst, r.total, r.plan, r.period_from.isoformat(), r.period_to.isoformat()])
    return response
