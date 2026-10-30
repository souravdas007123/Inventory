"""The platform owner's side of plan payments: approve or reject UPI payments, and the Razorpay (autopay) webhook."""
import json
import logging
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from backend import tenancy

from . import billing, razorpay
from .models import AutoPay, PlanOrder
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


STATE_OF_EVENT = {'subscription.activated': 'active', 'subscription.cancelled': 'cancelled', 'subscription.halted': 'halted',
                  'subscription.completed': 'completed'}


def _day(stamp):
    """A Razorpay unix time as a date (None when it is missing or odd)."""
    try:
        return datetime.fromtimestamp(int(stamp), timezone.get_current_timezone()).date() if stamp else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Razorpay tells us a subscription was charged, stopped or failed. The signature is checked on the raw body. It is what
    extends a shop on every renewal, and the safety net for a customer who paid but closed the window before the page could
    confirm it; each payment is added only once."""
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
    name = event.get('event') if isinstance(event, dict) else None
    if name != 'subscription.charged' and name not in STATE_OF_EVENT:
        return HttpResponse('ignored')
    payload = event.get('payload') or {}
    sub = ((payload.get('subscription') or {}).get('entity')) or {}
    pay = ((payload.get('payment') or {}).get('entity')) or {}
    ap = AutoPay.objects.filter(razorpay_subscription_id=sub.get('id')).first() if sub.get('id') else None
    if ap is None:
        return HttpResponse('unknown subscription')
    next_charge = _day(sub.get('charge_at'))
    if name == 'subscription.charged':
        if not pay.get('id') or pay.get('amount') != razorpay.paise(ap.amount):
            log.error('Razorpay charge %s of %s does not match autopay %s (%s)', pay.get('id'), pay.get('amount'), ap.pk, ap.amount)
            return HttpResponse('amount differs')
        billing.charge(ap.pk, str(pay['id'])[:40], f"Autopay payment {pay['id']} (webhook)", next_charge)
        return HttpResponse('ok')
    ap, changed = billing.autopay_state(ap.pk, STATE_OF_EVENT[name], next_charge if name == 'subscription.activated' else None)
    if changed and name == 'subscription.halted':
        billing.mail_autopay_failed(ap)
    return HttpResponse('ok')
