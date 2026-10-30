"""Razorpay Subscriptions (auto-renewing plans) without any extra package: make a subscription, cancel it, check the signatures.
Switched on only when RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set (and many shops is on). A plan can be bought only when its
Razorpay plan id (RAZORPAY_PLAN_MONTHLY / RAZORPAY_PLAN_YEARLY) is set too. The secret never reaches a browser."""
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
API = 'https://api.razorpay.com/v1'
TOTAL_COUNT = {'monthly': 120, 'yearly': 10}                  # how many charges at most (10 years)
PERIOD = {'monthly': 'monthly', 'yearly': 'yearly'}


class RazorpayError(Exception):
    """The call failed. The message is safe to show to the shop."""


def configured():
    return bool(getattr(settings, 'MULTI_SHOP', False) and getattr(settings, 'RAZORPAY_KEY_ID', '')
                and getattr(settings, 'RAZORPAY_KEY_SECRET', ''))


def plan_id(plan):
    return {'monthly': getattr(settings, 'RAZORPAY_PLAN_MONTHLY', ''), 'yearly': getattr(settings, 'RAZORPAY_PLAN_YEARLY', '')}.get(plan, '')


def available(plan):
    """True when this plan can be bought with autopay."""
    return configured() and bool(plan_id(plan))


def paise(amount):
    return int((Decimal(amount) * 100).to_integral_value())


def _call(path, payload=None):
    """POST (or GET when there is no payload) to Razorpay; returns the JSON answer as a dict."""
    token = base64.b64encode(f'{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}'.encode()).decode()
    headers = {'Authorization': f'Basic {token}'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'{API}/{path}', data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            detail = ''
        log.error('Razorpay %s answered %s: %s', path, e.code, detail)
        raise RazorpayError('The payment service did not accept the request. Please try again, or write to us.')
    except (urllib.error.URLError, OSError, ValueError):
        log.exception('Razorpay %s could not be reached', path)
        raise RazorpayError('We could not reach the payment service. Please try again in a minute.')
    if not isinstance(out, dict):
        raise RazorpayError('The payment service gave an unexpected answer. Please try again.')
    return out


def check_plan(plan, price):
    """Refuses to go on when the Razorpay plan is not the one we show: same amount (rupees, GST included), same period, every 1 period."""
    info = _call(f'plans/{plan_id(plan)}')
    item = info.get('item') or {}
    if (info.get('period') != PERIOD[plan] or info.get('interval') != 1 or item.get('amount') != paise(price)
            or (item.get('currency') or 'INR') != 'INR'):
        log.error('Razorpay plan %s (%s %s, amount %s) does not match the %s price %s', plan_id(plan), info.get('interval'),
                  info.get('period'), item.get('amount'), plan, price)
        raise RazorpayError('Autopay is not ready for this plan yet. Please pay by UPI or write to us.')


def create_subscription(plan, notes=None):
    """Makes a subscription for the plan and returns its id."""
    out = _call('subscriptions', dict(plan_id=plan_id(plan), total_count=TOTAL_COUNT[plan], customer_notify=1, notes=notes or {}))
    sub_id = out.get('id')
    if not sub_id:
        raise RazorpayError('The payment service did not accept the request. Please try again.')
    return str(sub_id)


def cancel_subscription(sub_id, at_cycle_end=True):
    """Stops further charges. The period already paid for stays valid either way."""
    _call(f'subscriptions/{sub_id}/cancel', dict(cancel_at_cycle_end=1 if at_cycle_end else 0))


def _same(expected, given):
    return hmac.compare_digest(expected.encode(), (given or '').encode())


def subscription_signature_ok(payment_id, subscription_id, signature, secret=None):
    """The check Razorpay documents for a finished subscription checkout: HMAC-SHA256 of 'payment_id|subscription_id'."""
    secret = secret or getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    if not (secret and payment_id and subscription_id and signature):
        return False
    return _same(hmac.new(secret.encode(), f'{payment_id}|{subscription_id}'.encode(), hashlib.sha256).hexdigest(), signature)


def webhook_signature_ok(body, signature, secret=None):
    """HMAC-SHA256 of the raw request body with the webhook secret (header X-Razorpay-Signature)."""
    secret = secret or getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
    if not (secret and signature):
        return False
    return _same(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature)
