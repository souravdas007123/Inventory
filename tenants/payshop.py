"""A shop owner pays for a plan: online with Razorpay, or by UPI (QR code + the UPI reference number, approved later by the
platform owner). Only on a shop's own address, only for the owner. These pages stay open when the plan has ended (the
shop must be able to pay to continue): see ShopAccessMiddleware."""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods, require_POST

from backend import access, qr, tenancy
from backend.views import page

from . import billing, razorpay, site, upi
from .models import PlanOrder

log = logging.getLogger(__name__)
MAX_ORDERS_PER_DAY = 10


def _shop(request):
    shop = getattr(request, 'tenant', None)
    if not tenancy.on_shop_site() or shop is None or shop.schema_name == settings.PUBLIC_SCHEMA_NAME:
        raise Http404
    return shop


def _plan(request):
    plan = request.POST.get('plan')
    return plan if plan in ('monthly', 'yearly') else None


def _too_many(shop):
    return PlanOrder.objects.filter(shop=shop, created_at__gte=timezone.now() - timedelta(days=1)).count() >= MAX_ORDERS_PER_DAY


@access.owner_required
@require_POST
def online_start(request):
    """Makes the order at Razorpay and returns what the checkout window needs (JSON)."""
    shop = _shop(request)
    if not razorpay.configured():
        raise Http404
    plan = _plan(request)
    if not plan:
        return JsonResponse(dict(ok=False, error='Choose monthly or yearly.'), status=400)
    if _too_many(shop):
        return JsonResponse(dict(ok=False, error='Too many payment attempts today. Please try again tomorrow or write to us.'), status=429)
    order = PlanOrder.objects.create(shop=shop, plan=plan, amount=billing.price_for(plan), method='razorpay',
                                     reference=billing.new_reference())
    try:
        order.razorpay_order_id = razorpay.create_order(order.reference, order.amount, dict(shop=shop.schema_name, reference=order.reference))
    except razorpay.RazorpayError as e:
        order.status, order.note = 'cancelled', str(e)[:200]
        order.save()
        return JsonResponse(dict(ok=False, error=str(e)), status=502)
    order.save()
    return JsonResponse(dict(
        ok=True, key=settings.RAZORPAY_KEY_ID, order_id=order.razorpay_order_id, amount=razorpay.paise(order.amount), currency='INR',
        name='MedSync', description=f'{billing.PLAN_LABEL[plan]} plan for {shop.name}', reference=order.reference,
        prefill=dict(name=shop.owner_name or '', email=shop.email or '', contact=shop.phone or '')))


@access.owner_required
@require_POST
def online_verify(request):
    """The checkout finished in the browser: check Razorpay's signature, then extend the plan (once)."""
    shop = _shop(request)
    order_id, payment_id = request.POST.get('razorpay_order_id', ''), request.POST.get('razorpay_payment_id', '')
    order = PlanOrder.objects.filter(shop=shop, method='razorpay', razorpay_order_id=order_id).first() if order_id else None
    if order is None or not razorpay.checkout_signature_ok(order_id, payment_id, request.POST.get('razorpay_signature', '')):
        return JsonResponse(dict(ok=False, error='We could not confirm this payment. If money was taken from your account, '
                                                 'do not pay again: it will be added to your plan shortly, or write to us.'), status=400)
    PlanOrder.objects.filter(pk=order.pk).update(razorpay_payment_id=payment_id[:40])
    billing.settle(order.pk, f'Online payment {payment_id}', allowed=('created', 'waiting', 'cancelled'))
    order.refresh_from_db()
    if order.status != 'paid':
        return JsonResponse(dict(ok=False, error='This payment could not be added. Please write to us with the reference ' + order.reference), status=409)
    messages.success(request, f'Payment received. Your plan is paid until {order.payment.period_to:%d %b %Y}. Thank you!')
    return JsonResponse(dict(ok=True))


@access.owner_required
@require_POST
def upi_start(request):
    """Starts (or continues) a UPI payment for a plan and opens its QR page."""
    shop = _shop(request)
    if not site.upi():
        raise Http404
    plan = _plan(request)
    if not plan:
        messages.error(request, 'Choose monthly or yearly.')
        return redirect('plan')
    order = PlanOrder.objects.filter(shop=shop, method='upi', status='created', plan=plan,
                                     created_at__gte=timezone.now() - timedelta(days=1)).first()
    if order is None:
        if _too_many(shop):
            messages.error(request, 'Too many payment attempts today. Please try again tomorrow or write to us.')
            return redirect('plan')
        order = PlanOrder.objects.create(shop=shop, plan=plan, amount=billing.price_for(plan), method='upi', reference=billing.new_reference())
    return redirect('plan_upi', pk=order.pk)


@access.owner_required
@require_http_methods(['GET', 'POST'])
def upi_page(request, pk):
    shop = _shop(request)
    order = get_object_or_404(PlanOrder, pk=pk, shop=shop, method='upi')
    target = site.upi()
    if target is None:
        raise Http404
    if order.status != 'created':
        messages.info(request, 'This payment was already sent to us.' if order.status == 'waiting' else 'This payment is closed.')
        return redirect('plan')
    error = ''
    if request.method == 'POST':
        utr, error = upi.clean_utr(request.POST.get('utr'))
        if not error and PlanOrder.objects.filter(utr=utr).exclude(pk=order.pk).exclude(status__in=('rejected', 'cancelled')).exists():
            error = 'This UPI reference number was already used for another payment.'
        if not error:
            if PlanOrder.objects.filter(pk=order.pk, status='created').update(status='waiting', utr=utr) == 1:
                order.refresh_from_db()
                billing.mail_platform_waiting(order, f"{request.scheme}://{settings.SHOP_BASE_DOMAIN}{tenancy.port_suffix(request)}/superadmin/payments/")
                messages.success(request, 'Thank you. We will check the payment and extend your plan, usually within a few hours. You will get an e-mail.')
            return redirect('plan')
    link = upi.pay_link(target[0], target[1], order.amount, order.reference, billing.PLAN_LABEL[order.plan])
    return page(request, 'app/plan_upi.html', active='plan', order=order, upi_id=target[0], upi_name=target[1], link=link,
                qr_svg=mark_safe(qr.svg(link)), error=error, utr=request.POST.get('utr', ''))


@access.owner_required
@require_POST
def order_cancel(request, pk):
    shop = _shop(request)
    PlanOrder.objects.filter(pk=pk, shop=shop, status='created').update(status='cancelled')
    messages.info(request, 'The payment was cancelled.')
    return redirect('plan')
