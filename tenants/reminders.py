"""Reminder e-mails to shop owners: the free trial or the paid plan is about to end, or has just ended.
Run once a day by `shops_daily` (or `send_plan_reminders`). Each reminder is sent once per end date."""
import logging
import re
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from backend import tenancy

from . import site
from .models import Shop

log = logging.getLogger(__name__)


def shop_address(shop):
    """https://<shop>.<base domain>/ (http while testing on localhost). '' if the shop has no address yet."""
    dom = shop.domains.filter(is_primary=True).first() or shop.domains.first()
    if dom is None:
        return ''
    local = dom.domain.endswith('localhost')
    return f"{'http' if local else 'https'}://{dom.domain}/"


def _contact_lines():
    data = site.current()
    lines = []
    number = re.sub(r'\D', '', data.get('whatsapp') or '')
    if 10 <= len(number) <= 15:
        lines.append(f'WhatsApp: https://wa.me/{number}?text={quote("Hi, I want to continue with MedSync")}')
    if data.get('contact_email'):
        lines.append(f"Email: {data['contact_email']}")
    return lines or ['Please contact MedSync to continue.']


def _when(left):
    return 'today' if left <= 0 else 'tomorrow' if left == 1 else f'in {left} days'


def message_for(shop, kind, end, today=None):
    """(subject, body) of the reminder."""
    today = today or timezone.localdate()
    prices = site.prices()
    left = (end - today).days
    trial = kind.startswith('trial')
    what = 'free trial' if trial else 'plan'
    if kind.endswith('_end'):
        subject = f'Your MedSync {what} has ended'
        status = (f'The {what} for your shop "{shop.name}" ended on {end:%d %b %Y}. Your data is safe and you can still sign in '
                  'and view everything, but saving new bills and entries is switched off until you choose a plan.')
    else:
        subject = f'Your MedSync {what} ends {_when(left)}'
        status = (f'The {what} for your shop "{shop.name}" ends {_when(left)}, on {end:%d %b %Y}. After that you can still '
                  'view your data, but saving new bills and entries is switched off until you choose a plan.')
    lines = [f'Hello {shop.owner_name or "there"},', '', status, '',
             f'Plans: Rs {prices["monthly"]} a month or Rs {prices["yearly"]} a year. Every plan has every feature.', '',
             'To continue, contact us:'] + _contact_lines()
    address = shop_address(shop)
    if address:
        lines += ['', f'Your shop: {address}']
    lines += ['', 'MedSync']
    return subject, '\n'.join(lines)


def send_due(today=None, dry_run=False):
    """Sends every reminder that is due and not sent yet. Returns {'sent': [...], 'failed': [...], 'checked': n}.
    Must run on the main site (public schema)."""
    today = today or timezone.localdate()
    out = {'sent': [], 'failed': [], 'checked': 0}
    for shop in Shop.objects.exclude(schema_name=settings.PUBLIC_SCHEMA_NAME).order_by('pk'):
        out['checked'] += 1
        due = tenancy.reminder_due(shop, today)
        if not due:
            continue
        kind, end = due
        key = tenancy.reminder_key(kind, end)
        if shop.last_reminder == key or not shop.email:
            continue
        label = f'{shop.schema_name}:{kind}'
        if dry_run:
            out['sent'].append(label)
            continue
        try:
            subject, body = message_for(shop, kind, end, today)
            send_mail(subject, body, None, [shop.email], fail_silently=False)
        except Exception:
            log.exception('Could not send the %s reminder to %s', kind, shop.schema_name)
            out['failed'].append(label)
            continue
        Shop.objects.filter(pk=shop.pk).update(last_reminder=key)
        out['sent'].append(label)
    return out
