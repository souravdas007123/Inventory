"""E-mails the owner when a staff member (not an owner) does something sensitive: deletes a record, edits a bill or an invoice,
makes a return, writes stock off, or applies a stock audit. Called from the audit log, so it never blocks the work itself.

At most 3 e-mails per person in 10 minutes, then one "more follows" e-mail, then silence: a clean-up of fifty records is
not fifty e-mails. Everything is still in the Audit Log."""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

log = logging.getLogger(__name__)

ACTIONS = {'DELETE', 'CANCEL', 'RETURN', 'WRITEOFF', 'STOCK-AUDIT', 'FIX', 'RESET'}
EDITED = {'Bill', 'Purchase Invoice', 'Payment', 'Receipt', 'Sales Return', 'Purchase Return', 'Write-off'}
WINDOW = timedelta(minutes=10)
FULL_MAILS = 3
ASYNC = True            # tests turn this off


def sensitive(action, target):
    return action in ACTIONS or (action == 'EDIT' and target in EDITED)


def owner_emails():
    from django.contrib.auth import get_user_model
    from . import access
    from .models import ShopProfile
    found = []
    shop = ShopProfile.objects.first()
    if shop and (shop.email or '').strip():
        found.append(shop.email.strip())
    for u in get_user_model().objects.filter(is_active=True).exclude(email=''):
        if access.is_owner(u) and u.email.strip() not in found:
            found.append(u.email.strip())
    return found


def _deliver(subject, body, to):
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=True)
    except Exception:
        log.exception('Could not send the staff action alert')


def _send(subject, body, to):
    if ASYNC:
        threading.Thread(target=_deliver, args=(subject, body, to), daemon=True).start()
    else:
        _deliver(subject, body, to)


def after(user, action, target, detail):
    """Called after an audit log entry is written. `user` is the person (a User object); anything else is ignored."""
    from . import access
    from .models import AuditLog, ShopProfile
    if not hasattr(user, 'get_username') or not sensitive(action, target) or access.is_owner(user):
        return False
    name = user.get_username()
    recent = [r for r in AuditLog.objects.filter(user=name, created_at__gte=timezone.now() - WINDOW).order_by('-id')[:60]
              if sensitive(r.action, r.target)]
    n = len(recent)
    if n > FULL_MAILS + 1:
        return False
    to = owner_emails()
    if not to:
        return False
    shop = (ShopProfile.objects.first().name if ShopProfile.objects.exists() else '') or 'Symaso'
    if n == FULL_MAILS + 1:
        _send(f'[{shop}] {name} is making many changes',
              f'{name} has made several sensitive changes in the last 10 minutes. No more alerts will be sent for this.\n\n'
              'Open the Audit Log in your shop to see all of them.', to)
        return True
    when = timezone.localtime().strftime('%d %b %Y, %H:%M')
    _send(f'[{shop}] Staff action: {name} - {action} {target}',
          f'{name} (staff) did this in your shop:\n\n  Action: {action}\n  On: {target}\n  Details: {detail or "-"}\n  When: {when}\n\n'
          'If you do not recognise this, open the Audit Log in your shop and check with them.', to)
    return True
