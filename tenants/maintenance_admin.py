"""The platform owner's Maintenance page: plan a window, start it now, extend it, end it. Shops are told by a banner (and, when asked, by
e-mail); during the window shops show the update page while these platform pages keep working."""
import logging
import time
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from backend import tenancy

from . import mails, maintenance
from .models import Shop
from .platform import platform_admin_required

log = logging.getLogger(__name__)
EMAIL_BUDGET_SECONDS = 20


def owners():
    """[(shop)] one per distinct e-mail address; shops that are switched off are left out."""
    seen, out = set(), []
    for shop in Shop.objects.exclude(schema_name='public').order_by('pk'):
        email = (shop.email or '').strip().lower()
        if not email or email in seen or tenancy.shop_state(shop) == 'paused':
            continue
        seen.add(email)
        out.append(shop)
    return out


def email_owners(window, budget=EMAIL_BUDGET_SECONDS):
    """Sends the notice to every shop owner, one by one. Returns (sent, failed, left_over); stops after `budget` seconds."""
    sent = failed = 0
    people = owners()
    started = time.monotonic()
    for i, shop in enumerate(people):
        if time.monotonic() - started > budget:
            return sent, failed, len(people) - i
        try:
            subject, text, html = mails.maintenance_message(shop, window)
            mails.send(subject, text, html, shop.email)
            sent += 1
        except Exception:
            log.exception('Could not send the maintenance notice to %s', shop.schema_name)
            failed += 1
    return sent, failed, 0


def _tell(request, wanted):
    """After a window was saved: e-mail the owners when asked, and say what happened."""
    if not wanted:
        return
    window = maintenance.window()
    if window is None:
        return
    sent, failed, left = email_owners(window)
    maintenance.mark_emailed()
    text = f'E-mail sent to {sent} shop owner{"" if sent == 1 else "s"}.'
    if failed:
        text += f' {failed} could not be sent.'
    if left:
        text += f' {left} more were not tried because it took too long.'
    (messages.warning if failed or left else messages.success)(request, text)


@platform_admin_required
def maintenance_page(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'schedule':
            start, end, message, errors = maintenance.parse_schedule(request.POST)
            for e in errors:
                messages.error(request, e)
            if not errors:
                maintenance.schedule(start, end, message)
                messages.success(request, f'Maintenance planned: {maintenance.fmt(start)} to {maintenance.fmt(end)}. '
                                          'Shops now see a banner about it.')
                _tell(request, request.POST.get('email'))
        elif action == 'start':
            minutes, problem = maintenance.clean_minutes(request.POST.get('minutes'))
            message, bad = maintenance.clean_message(request.POST.get('message'))
            if not request.POST.get('sure'):
                messages.error(request, 'Tick the box to confirm that shops should be switched to the update page now.')
            elif problem or bad:
                messages.error(request, problem or bad)
            else:
                maintenance.start_now(minutes, message)
                messages.success(request, 'Maintenance is ON. Shops see the update page. Press "End maintenance" when you are done.')
                _tell(request, request.POST.get('email'))
        elif action == 'extend':
            minutes, problem = maintenance.clean_minutes(request.POST.get('minutes') or 15)
            if problem:
                messages.error(request, problem)
            else:
                end = maintenance.extend(minutes)
                if end is None:
                    messages.error(request, 'There is no maintenance to extend.')
                else:
                    messages.success(request, f'Maintenance now ends at {maintenance.fmt(end)}.')
        elif action == 'end':
            if maintenance.end_now():
                messages.success(request, 'Maintenance is off. Shops open again.')
            else:
                messages.info(request, 'Maintenance was already off.')
        return redirect('platform_maintenance')
    kind, data = maintenance.state()
    soon = timezone.localtime(timezone.now() + timedelta(hours=1))
    return render(request, 'app/platform_maintenance.html', dict(
        kind=kind, data=data, start_text=maintenance.fmt(data['start']) if data else '', end_text=maintenance.fmt(data['end']) if data else '',
        default_date=f'{soon:%Y-%m-%d}', default_time='23:00', shops=len(owners()),
        min_minutes=maintenance.MIN_MINUTES, max_minutes=maintenance.MAX_MINUTES))
