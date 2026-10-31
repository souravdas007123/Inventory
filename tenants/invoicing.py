"""Making the tax invoice of a payment. Called inside the same transaction that saves the Payment, so a payment that is rolled
back leaves no invoice and no number is lost."""
from django.conf import settings
from django.db import transaction

from . import gst, site
from .models import Invoice, InvoiceCounter, SiteSetting

PLAN_NAME = {'monthly': 'Monthly', 'yearly': 'Yearly'}


def financial_year(day):
    """'2026-27' for any day from 1 April 2026 to 31 March 2027."""
    start = day.year if day.month >= 4 else day.year - 1
    return f'{start}-{(start + 1) % 100:02d}'


def next_number(day):
    """Takes the next number of the day's financial year, e.g. MS/2026-27/0001 (15 characters: GST allows 16)."""
    fy = financial_year(day)
    with transaction.atomic():
        InvoiceCounter.objects.get_or_create(fy=fy)
        counter = InvoiceCounter.objects.select_for_update().get(fy=fy)
        counter.last += 1
        counter.save(update_fields=['last'])
    return f'{getattr(settings, "INVOICE_PREFIX", "MS")}/{fy}/{counter.last:04d}', fy


def buyer_of(shop):
    """What the invoice says about the customer: the billing details he saved, else what we know. A shop that never gave a
    country is taken as Indian, in the seller's own state."""
    country = shop.bill_country or 'IN'
    return dict(name=shop.bill_name or shop.name, address=shop.bill_address, country=country,
                state=shop.bill_state if country == 'IN' else '', tax_id=shop.bill_tax_id)


def billing_ready(shop):
    """True when the customer has given everything an invoice needs (name, address, country and, in India, the state)."""
    return bool(shop.bill_name and shop.bill_address and shop.bill_country and (shop.bill_country != 'IN' or shop.bill_state))


SELLER_FIELDS = ('seller_name', 'seller_address', 'seller_gstin', 'seller_lut', 'seller_sac')


def seller_now():
    """The seller details read straight from the database (never from the 60-second cache): an invoice is a legal paper and
    must carry what is saved at this moment."""
    row = SiteSetting.objects.filter(pk=1).first()
    return {f: (getattr(row, f) if row else '') for f in SELLER_FIELDS}


def split_for(shop, total, seller=None):
    """The tax split a payment of `total` by this shop would get (also used by the popup before paying, which may use the
    cached seller details)."""
    seller = seller if seller is not None else site.current()
    buyer = buyer_of(shop)
    state = buyer['state'] or gst.state_of(seller.get('seller_gstin'))       # state unknown: the seller's own state
    return gst.tax_for(total, seller.get('seller_gstin') or '', buyer['country'], state)


def create_invoice(shop, payment, plan, amount, today):
    seller = seller_now()
    buyer = buyer_of(shop)
    parts = split_for(shop, amount, seller)
    number, fy = next_number(today)
    return Invoice.objects.create(
        number=number, fy=fy, date=today, shop=shop, payment=payment, plan=plan,
        description=f'MedSync {PLAN_NAME.get(plan, plan)} plan (software as a service)',
        period_from=payment.period_from, period_to=payment.period_to,
        seller_name=seller.get('seller_name') or '', seller_address=seller.get('seller_address') or '',
        seller_gstin=seller.get('seller_gstin') or '', seller_lut=seller.get('seller_lut') or '', sac=seller.get('seller_sac') or '',
        buyer_name=buyer['name'], buyer_address=buyer['address'], buyer_country=buyer['country'], buyer_state=buyer['state'],
        buyer_tax_id=buyer['tax_id'], kind=parts['kind'], rate=parts['rate'], taxable=parts['taxable'], cgst=parts['cgst'],
        sgst=parts['sgst'], igst=parts['igst'], total=parts['total'])
