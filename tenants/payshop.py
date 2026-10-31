"""A shop owner pays for a plan: with Razorpay autopay (a subscription that renews on its own), or by UPI (QR code + the UPI reference number, approved later by the
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

from . import billing, invoicing, razorpay, site, upi
from .models import AutoPay, PlanOrder

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
def autopay_start(request):
    """Makes the subscription at Razorpay and returns what the checkout window needs (JSON). The price is the server's."""
    shop = _shop(request)
    plan = _plan(request)
    if not plan:
        return JsonResponse(dict(ok=False, error='Choose monthly or yearly.'), status=400)
    if not razorpay.available(plan):
        raise Http404
    if not invoicing.billing_ready(shop):
        return JsonResponse(dict(ok=False, error='Please save your invoice details first (on this page, under Details for your invoice).'), status=400)
    if AutoPay.objects.filter(shop=shop, status='active').exists():
        return JsonResponse(dict(ok=False, error='Autopay is already on for this shop. To change the plan, stop the autopay first.'), status=409)
    if AutoPay.objects.filter(shop=shop, created_at__gte=timezone.now() - timedelta(days=1)).count() >= MAX_ORDERS_PER_DAY:
        return JsonResponse(dict(ok=False, error='Too many payment attempts today. Please try again tomorrow or write to us.'), status=429)
    price = billing.price_for(plan)
    try:
        razorpay.check_plan(plan, price)
        for old in AutoPay.objects.filter(shop=shop, status='created'):                 # a checkout that was never finished
            try:
                razorpay.cancel_subscription(old.razorpay_subscription_id, at_cycle_end=False)
            except razorpay.RazorpayError:
                pass
            AutoPay.objects.filter(pk=old.pk, status='created').update(status='cancelled', ended_at=timezone.now())
        sub_id = razorpay.create_subscription(plan, dict(shop=shop.schema_name, plan=plan))
    except razorpay.RazorpayError as e:
        return JsonResponse(dict(ok=False, error=str(e)), status=502)
    AutoPay.objects.create(shop=shop, plan=plan, amount=price, razorpay_subscription_id=sub_id)
    return JsonResponse(dict(
        ok=True, key=settings.RAZORPAY_KEY_ID, subscription_id=sub_id, name='MedSync',
        description=f'{billing.PLAN_LABEL[plan]} plan for {shop.name} (renews every {"month" if plan == "monthly" else "year"})',
        prefill=dict(name=shop.owner_name or '', email=shop.email or '', contact=shop.phone or '')))


@access.owner_required
@require_POST
def autopay_verify(request):
    """The checkout finished in the browser: check Razorpay's signature, then extend the plan (once)."""
    shop = _shop(request)
    sub_id, payment_id = request.POST.get('razorpay_subscription_id', ''), request.POST.get('razorpay_payment_id', '')
    ap = AutoPay.objects.filter(shop=shop, razorpay_subscription_id=sub_id).first() if sub_id else None
    if ap is None or not razorpay.subscription_signature_ok(payment_id, sub_id, request.POST.get('razorpay_signature', '')):
        return JsonResponse(dict(ok=False, error='We could not confirm this payment. If money was taken from your account, '
                                                 'do not pay again: it will be added to your plan shortly, or write to us.'), status=400)
    order = billing.charge(ap.pk, payment_id[:40], f'Autopay payment {payment_id}')
    if order.status != 'paid':
        return JsonResponse(dict(ok=False, error='This payment could not be added. Please write to us with the reference ' + order.reference), status=409)
    messages.success(request, f'Payment received. Your plan is paid until {order.payment.period_to:%d %b %Y} and renews on its own. Thank you!')
    return JsonResponse(dict(ok=True))


@access.owner_required
@require_POST
def autopay_cancel(request, pk):
    """Stops the renewals. What was paid for stays valid until its end date."""
    shop = _shop(request)
    ap = get_object_or_404(AutoPay, pk=pk, shop=shop)
    if ap.status not in ('active', 'created'):
        messages.info(request, 'This autopay is already stopped.')
        return redirect('plan')
    try:
        razorpay.cancel_subscription(ap.razorpay_subscription_id, at_cycle_end=True)
    except razorpay.RazorpayError as e:
        messages.error(request, f'{e} The autopay was not stopped.')
        return redirect('plan')
    billing.autopay_state(ap.pk, 'cancelled')
    messages.success(request, 'Autopay stopped. You will not be charged again. Your plan stays valid until its end date.')
    return redirect('plan')


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
    if not invoicing.billing_ready(shop):
        messages.error(request, 'Please save your invoice details first (under Details for your invoice).')
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
