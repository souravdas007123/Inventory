"""The shop owner's own 'Plan and payments' page (/plan/, on a shop's address only): the state of the free trial or the
paid plan, the days left, the prices, how to pay, and the list of payments MedSync has recorded for this shop."""
import re
from urllib.parse import quote

from django.http import Http404
from django.utils import timezone

from backend import access, tenancy
from backend.views import page

from . import site
from .platform import STATE_LABELS


@access.owner_required
def plan(request):
    if not tenancy.on_shop_site():
        raise Http404                                   # the main site has no plan
    shop = request.tenant
    today = timezone.localdate()
    state = tenancy.shop_state(shop, today)
    price, data = site.prices(), site.current()
    number = re.sub(r'\D', '', data.get('whatsapp') or '')
    wa = (f"https://wa.me/{number}?text={quote(f'Hi, I want to continue with MedSync for {shop.name}')}"
          if 10 <= len(number) <= 15 else '')
    on_trial = shop.plan == 'trial'
    saving = price['monthly'] * 12 - price['yearly']
    return page(request, 'app/plan.html', active='plan', shop=shop, state=state, state_label=STATE_LABELS[state],
                end=shop.trial_ends if on_trial else shop.paid_until, on_trial=on_trial,
                days_left=tenancy.days_left(shop, today), prices=price, saving=saving if saving > 0 else 0,
                whatsapp=wa, contact_email=data.get('contact_email') or '', payments=shop.payments.all()[:50])
