"""Recording what a shop pays: the one place that moves a shop's paid-until date.

Used by the platform panel's *Save payment* button, by the Razorpay confirmation and by the approval of a UPI payment. An order
can be settled only once (the row is locked), so a double click, the browser confirmation and Razorpay's webhook arriving
together can never extend a shop twice."""
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from backend import tenancy

from . import site
from .models import Payment, PlanOrder, Shop

log = logging.getLogger(__name__)
PLAN_LABEL = {'monthly': 'Monthly', 'yearly': 'Yearly'}
REF_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'          # no 0/O or 1/I, easy to read out and type


def log_note(shop, text):
    shop.notes = f'{timezone.localdate():%Y-%m-%d}: {text}\n{shop.notes}'


def price_for(plan):
    return Decimal(site.prices()[plan])


def record_payment(shop, plan, amount, today, note=''):
    """Saves a Payment and moves the shop's plan and paid-until date (a payment before the end of a period extends it).
    The caller saves the shop (inside its own transaction)."""
    start = max(today, shop.paid_until or today)
    end = tenancy.add_months(start, 1 if plan == 'monthly' else 12)
    payment = Payment.objects.create(shop=shop, plan=plan, amount=amount, paid_on=today, period_from=start, period_to=end,
                                     note=(note or '')[:200])
    shop.plan, shop.paid_until = plan, end
    log_note(shop, f'payment Rs {amount} for {plan} plan, paid until {end:%d %b %Y}')
    return payment


def new_reference():
    while True:
        ref = 'MS' + ''.join(secrets.choice(REF_CHARS) for _ in range(8))
        if not PlanOrder.objects.filter(reference=ref).exists():
            return ref


def settle(order_pk, note, allowed=('created', 'waiting'), today=None):
    """Marks the order paid and extends the shop, once. Returns the order when it was settled just now, None when it was
    not in a state that can be paid (already paid, rejected ...)."""
    with transaction.atomic():
        order = PlanOrder.objects.select_for_update().get(pk=order_pk)
        if order.status not in allowed:
            return None
        shop = Shop.objects.select_for_update().get(pk=order.shop_id)
        payment = record_payment(shop, order.plan, order.amount, today or timezone.localdate(), note)
        shop.save()
        order.status, order.payment, order.decided_at, order.note = 'paid', payment, timezone.now(), (note or '')[:200]
        order.save()
    cache.delete(f'shop-stats:{shop.schema_name}')
    mail_receipt(order)
    return order


def reject(order_pk, reason):
    """A waiting UPI payment that was not found. Returns the order, or None when it was no longer waiting."""
    with transaction.atomic():
        order = PlanOrder.objects.select_for_update().get(pk=order_pk)
        if order.status != 'waiting':
            return None
        order.status, order.decided_at, order.note = 'rejected', timezone.now(), (reason or '')[:200]
        order.save()
    mail_rejected(order)
    return order


def _mail(subject, text, to):
    to = [a for a in to if a]
    if not to:
        return
    try:
        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    except Exception:
        log.exception('Could not send the e-mail "%s"', subject)


def mail_receipt(order):
    payment = order.payment
    until = f'{payment.period_to:%d %b %Y}' if payment else ''
    _mail(f'MedSync payment received: Rs {order.amount}',
          f'Hello {order.shop.owner_name or "there"},\n\nThank you. We received your payment for {order.shop.name}.\n\n'
          f'Plan: {PLAN_LABEL.get(order.plan, order.plan)}\nAmount: Rs {order.amount} (GST included)\nReference: {order.reference}\n'
          f'Your shop is paid until: {until}\n\nMedSync', [order.shop.email])


def mail_rejected(order):
    _mail('MedSync could not find your UPI payment',
          f'Hello {order.shop.owner_name or "there"},\n\nWe could not match your UPI payment (reference {order.reference}, UPI number {order.utr}) '
          f'for {order.shop.name}.\n\nReason: {order.note or "not found in our account"}\n\n'
          'Please check the number and send it again from the Plan and payments page, or write to us.\n\nMedSync', [order.shop.email])


def mail_platform_waiting(order, link):
    to = list(getattr(settings, 'ALERT_EMAILS', []) or []) or [site.current().get('contact_email') or '']
    _mail(f'[MedSync] UPI payment waiting for approval: {order.shop.name} Rs {order.amount}',
          f'Shop: {order.shop.name}\nPlan: {PLAN_LABEL.get(order.plan, order.plan)}\nAmount: Rs {order.amount}\n'
          f'UPI reference (UTR): {order.utr}\nOrder reference: {order.reference}\n\nCheck your bank / UPI app, then approve or reject it here:\n{link}\n', to)
