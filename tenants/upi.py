"""The UPI payment link (also the content of the QR code) and the check of the UPI reference number the owner types."""
import re
from decimal import Decimal
from urllib.parse import quote

UTR_RE = re.compile(r'\d{12}')


def clean_utr(raw):
    """(utr, '') for a 12-digit UPI reference number (spaces ignored), else ('', message)."""
    value = re.sub(r'\s+', '', raw or '')
    if not UTR_RE.fullmatch(value):
        return '', 'Enter the 12-digit UPI reference number (UTR) from your payment app, digits only.'
    return value, ''


def pay_link(upi_id, name, amount, reference, plan_label):
    """upi://pay?... with the amount fixed. `reference` and the plan go into the payment note so the platform owner sees them
    in his bank statement."""
    amount = Decimal(amount).quantize(Decimal('0.01'))
    note = f'MedSync {plan_label} {reference}'
    parts = (('pa', upi_id), ('pn', name[:30]), ('am', f'{amount}'), ('cu', 'INR'), ('tn', note))
    return 'upi://pay?' + '&'.join(f'{k}={quote(v, safe="@.-_")}' for k, v in parts)
