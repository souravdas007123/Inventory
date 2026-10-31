"""GST rules for an invoice, in one small testable place (no database).

* India -> a customer in the seller's own state pays CGST + SGST (half each); a customer in another state pays IGST.
* Any other country -> an export of service: zero-rated, no tax is added (the customer pays the same price).
* A seller without a GST number cannot charge GST: the invoice is a plain invoice without any tax.

The price a customer pays always includes the tax (the Razorpay plans have one fixed amount), so the tax is taken out of it."""
import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from .taxdata import COUNTRIES, INDIA_STATES

CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
GSTIN_RE = re.compile(r'^(\d{2})[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
CENT = Decimal('0.01')


def clean_gstin(text):
    return re.sub(r'\s+', '', (text or '')).upper()


def gstin_ok(gstin):
    """True for a well-formed GST number whose last character (the check character) is right and whose state code exists."""
    gstin = clean_gstin(gstin)
    m = GSTIN_RE.match(gstin)
    if not m or m.group(1) not in INDIA_STATES:
        return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        value = CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += value // 36 + value % 36
    return CHARS[(36 - total % 36) % 36] == gstin[14]


def state_of(gstin):
    """The two-digit state code at the start of a GST number ('' when it is not one)."""
    gstin = clean_gstin(gstin)
    return gstin[:2] if GSTIN_RE.match(gstin) else ''


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def tax_for(total, seller_gstin, buyer_country, buyer_state):
    """Splits a price that includes tax. Returns a dict: kind ('cgst_sgst', 'igst', 'export' or 'none'), rate (percent), taxable,
    cgst, sgst, igst, tax, total. The parts always add up to the total."""
    total = _money(total)
    zero = Decimal('0.00')
    out = dict(kind='none', rate=Decimal(0), taxable=total, cgst=zero, sgst=zero, igst=zero, tax=zero, total=total)
    seller_state = state_of(seller_gstin)
    if not seller_state:
        return out                                                       # a seller without a GST number charges no GST
    if (buyer_country or 'IN') != 'IN':
        out['kind'] = 'export'
        return out
    rate = Decimal(settings.GST_PERCENT)
    taxable = _money(total * 100 / (100 + rate))
    tax = total - taxable
    if buyer_state and buyer_state != seller_state:
        out.update(kind='igst', rate=rate, taxable=taxable, tax=tax, igst=tax)
        return out
    if int(tax * 100) % 2:                                               # CGST and SGST must be the same amount: move one paisa
        taxable, tax = taxable + CENT, tax - CENT                        # from the tax to the price so that both halves are equal
    out.update(kind='cgst_sgst', rate=rate, taxable=taxable, tax=tax, cgst=tax / 2, sgst=tax / 2)
    return out


def rows_for(parts, total_label='Total to pay now'):
    """The lines the 'before you pay' popup and the invoice show for a tax split: [(label, 'Rs 123.45'), ...]."""
    money = lambda v: f'Rs {v:.2f}'
    kind, rate = parts['kind'], Decimal(parts['rate']).normalize()
    half = rate / 2
    if kind == 'cgst_sgst':
        rows = [('Price without GST', money(parts['taxable'])), (f'CGST ({half:f}%)', money(parts['cgst'])), (f'SGST ({half:f}%)', money(parts['sgst']))]
    elif kind == 'igst':
        rows = [('Price without GST', money(parts['taxable'])), (f'IGST ({rate:f}%)', money(parts['igst']))]
    elif kind == 'export':
        rows = [('Price', money(parts['taxable'])), ('Tax (export of service, zero-rated)', 'Rs 0.00')]
    else:
        rows = [('Price', money(parts['taxable']))]
    return rows + [(total_label, money(parts['total']))]


def country_name(code):
    return COUNTRIES.get(code, code or '')


def clean_billing(post, seller_gstin=''):
    """Checks the shop's billing details form. Returns (values, errors); values are bill_name, bill_country, bill_state,
    bill_address, bill_tax_id. In India the state is needed (it decides CGST+SGST or IGST) and a GST number must be a real one
    from the same state; for another country the tax number (VAT / company number) is optional free text."""
    errors = []
    name = ' '.join((post.get('bill_name') or '').split())
    country = (post.get('bill_country') or '').strip().upper()
    state = (post.get('bill_state') or '').strip()
    address = '\n'.join(' '.join(line.split()) for line in (post.get('bill_address') or '').replace('\r', '').split('\n') if line.strip())
    tax_id = clean_gstin(post.get('bill_tax_id'))
    if not name or len(name) > 120:
        errors.append('Enter the name for the invoice (shop or business name), at most 120 characters.')
    if country not in COUNTRIES:
        errors.append('Choose the country.')
    if not address or len(address) > 300:
        errors.append('Enter the address for the invoice, at most 300 characters.')
    if country == 'IN':
        if state not in INDIA_STATES:
            errors.append('Choose the state: it decides whether CGST + SGST or IGST is charged.')
        if tax_id:
            if not gstin_ok(tax_id):
                errors.append('The GST number is not valid. It has 15 letters and digits, like 19ABCDE1234F1Z5. Leave it blank if you have none.')
            elif state in INDIA_STATES and state_of(tax_id) != state:
                errors.append('The GST number belongs to a different state than the one you chose.')
    else:
        state = ''
        if len(tax_id) > 30:
            errors.append('The tax number can have at most 30 characters.')
    return dict(bill_name=name, bill_country=country, bill_state=state, bill_address=address, bill_tax_id=tax_id), errors
