"""The platform owner's side of plan payments: approve or reject UPI payments, and the Razorpay webhook."""
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from backend import tenancy

from . import billing, razorpay
from .models import PlanOrder
from .platform import platform_admin_required

log = logging.getLogger(__name__)


@platform_admin_required
def payments(request):
    waiting = PlanOrder.objects.filter(status='waiting').select_related('shop').order_by('created_at')
    recent = PlanOrder.objects.exclude(status__in=('created', 'waiting')).select_related('shop')[:50]
    return render(request, 'app/platform_payments.html', dict(waiting=waiting, recent=recent))


@platform_admin_required
@require_POST
def approve(request, pk):
    order = get_object_or_404(PlanOrder, pk=pk)
    done = billing.settle(order.pk, f'UPI {order.utr}, approved by {request.user.get_username()}', allowed=('waiting',))
    if done is None:
        messages.error(request, 'This payment was already handled.')
    else:
        messages.success(request, f'Approved. {order.shop.name} is paid until {done.payment.period_to:%d %b %Y}.')
    return redirect('platform_payments')


@platform_admin_required
@require_POST
def reject(request, pk):
    order = get_object_or_404(PlanOrder, pk=pk)
    reason = ' '.join((request.POST.get('reason') or '').split())[:200] or 'We could not find this payment in our account.'
    if billing.reject(order.pk, reason) is None:
        messages.error(request, 'This payment was already handled.')
    else:
        messages.success(request, 'Rejected. The shop was told by e-mail.')
    return redirect('platform_payments')


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Razorpay tells us a payment was captured. The signature is checked on the raw body. It is the safety net for a customer
    who paid but closed the window before the page could confirm it; the order is settled only once."""
    if not tenancy.is_platform():
        raise Http404
    if not getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', ''):
        raise Http404
    body = request.body
    if not razorpay.webhook_signature_ok(body, request.headers.get('X-Razorpay-Signature', '')):
        return HttpResponse(status=400)
    try:
        event = json.loads(body.decode('utf-8'))
    except ValueError:
        return HttpResponse(status=400)
    if not isinstance(event, dict) or event.get('event') not in ('payment.captured', 'order.paid'):
        return HttpResponse('ignored')
    payload = event.get('payload') or {}
    pay = ((payload.get('payment') or {}).get('entity')) or {}
    entity = ((payload.get('order') or {}).get('entity')) or {}
    order_id = pay.get('order_id') or entity.get('id')
    order = PlanOrder.objects.filter(method='razorpay', razorpay_order_id=order_id).first() if order_id else None
    if order is None:
        return HttpResponse('unknown order')
    paid = pay.get('amount') if pay.get('amount') is not None else entity.get('amount_paid')
    if paid != razorpay.paise(order.amount):
        log.error('Razorpay amount %s does not match order %s (%s)', paid, order.reference, order.amount)
        return HttpResponse('amount differs')
    if pay.get('id'):
        PlanOrder.objects.filter(pk=order.pk).update(razorpay_payment_id=str(pay['id'])[:40])
    billing.settle(order.pk, f"Online payment {pay.get('id', '')} (webhook)", allowed=('created', 'waiting', 'cancelled'))
    return HttpResponse('ok')
