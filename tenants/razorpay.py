"""Razorpay (online payment) without any extra package: create an order, check the signatures. Switched on only when
RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set (and many shops is on). The secret never reaches a browser."""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings

log = logging.getLogger(__name__)
ORDERS_URL = 'https://api.razorpay.com/v1/orders'


class RazorpayError(Exception):
    """The order could not be made. The message is safe to show."""


def configured():
    return bool(getattr(settings, 'MULTI_SHOP', False) and getattr(settings, 'RAZORPAY_KEY_ID', '')
                and getattr(settings, 'RAZORPAY_KEY_SECRET', ''))


def paise(amount):
    return int((Decimal(amount) * 100).to_integral_value())


def create_order(reference, amount, notes=None):
    """Makes a Razorpay order for `amount` rupees (captured automatically when paid) and returns its id."""
    body = json.dumps(dict(amount=paise(amount), currency='INR', receipt=reference, payment_capture=1, notes=notes or {})).encode()
    token = base64.b64encode(f'{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}'.encode()).decode()
    req = urllib.request.Request(ORDERS_URL, data=body, headers={'Content-Type': 'application/json', 'Authorization': f'Basic {token}'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            order_id = json.loads(resp.read().decode('utf-8')).get('id')
    except (urllib.error.URLError, OSError, ValueError):
        log.exception('Razorpay did not make the order')
        raise RazorpayError('We could not reach the payment service. Please try again in a minute.')
    if not order_id:
        raise RazorpayError('The payment service did not accept the order. Please try again.')
    return str(order_id)


def _same(expected, given):
    return hmac.compare_digest(expected.encode(), (given or '').encode())


def checkout_signature_ok(order_id, payment_id, signature, secret=None):
    """The check Razorpay documents for a finished checkout: HMAC-SHA256 of 'order_id|payment_id' with the key secret."""
    secret = secret or getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    if not (secret and order_id and payment_id and signature):
        return False
    return _same(hmac.new(secret.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest(), signature)


def webhook_signature_ok(body, signature, secret=None):
    """HMAC-SHA256 of the raw request body with the webhook secret (header X-Razorpay-Signature)."""
    secret = secret or getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
    if not (secret and signature):
        return False
    return _same(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature)
