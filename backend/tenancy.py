"""Helpers for "many shops in one install" (see MULTI_SHOP in settings). Nothing here imports the shop models,
so every function can be used and tested whether the switch is on or off."""
import re
import secrets
from datetime import timedelta

from django.conf import settings

# Names a shop may not take as its web address (they are used by the platform itself or would look official).
RESERVED = frozenset({
    'www', 'admin', 'api', 'app', 'apps', 'mail', 'smtp', 'ftp', 'static', 'media', 'blog', 'support', 'help', 'login',
    'logout', 'signup', 'register', 'demo', 'test', 'testing', 'staging', 'dev', 'medsync', 'superadmin', 'super',
    'root', 'billing', 'payment', 'payments', 'dashboard', 'status', 'cdn', 'ns1', 'ns2', 'web', 'site', 'public',
})
_SUB_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{1,28})[a-z0-9]$')


def is_platform():
    """True on the main site (and always when many shops is off). False while a shop's own address is open."""
    if not getattr(settings, 'MULTI_SHOP', False):
        return True
    from django.db import connection
    return getattr(connection, 'schema_name', 'public') == getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')


def clean_subdomain(raw):
    """(name, '') when the web address name is fine, else ('', message). Letters, digits and hyphens, 3 to 30 characters."""
    name = (raw or '').strip().lower()
    if not name:
        return '', 'Choose a web address for your shop.'
    if not _SUB_RE.match(name):
        return '', 'Use 3 to 30 letters, digits or hyphens. It cannot start or end with a hyphen.'
    if '--' in name:
        return '', 'Please do not use two hyphens together.'
    if name in RESERVED:
        return '', 'This name is reserved. Please choose another.'
    return name, ''


def schema_name_for(subdomain):
    """The PostgreSQL schema of a shop: s_ plus the web address name (hyphens become underscores)."""
    return 's_' + subdomain.replace('-', '_')


def add_months(day, months):
    """The same day number some months later (the last day of that month when it has fewer days)."""
    import calendar
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def new_token():
    """The secret in a confirmation link: 22 letters and digits (88 random bits). It is kept short on purpose, so the
    whole link stays on one line of the email (long lines are broken by email programs and the link stops working)."""
    return secrets.token_hex(11)


def trial_end(today, days=None):
    return today + timedelta(days=days if days is not None else getattr(settings, 'TRIAL_DAYS', 30))


def shop_state(shop, today=None):
    """'paused', 'trial', 'active' or 'locked' for an object with is_paused, plan, trial_ends and paid_until."""
    from django.utils import timezone
    today = today or timezone.localdate()
    if getattr(shop, 'is_paused', False):
        return 'paused'
    paid_until = getattr(shop, 'paid_until', None)
    if paid_until and paid_until >= today:
        return 'active'
    if getattr(shop, 'plan', 'trial') == 'trial':
        ends = getattr(shop, 'trial_ends', None)
        if ends is None or ends >= today:
            return 'trial'
    return 'locked'


def days_left(shop, today=None):
    """Days until the trial or the paid period ends (0 on the last day); None when there is no end date."""
    from django.utils import timezone
    today = today or timezone.localdate()
    end = getattr(shop, 'paid_until', None) if getattr(shop, 'plan', 'trial') != 'trial' else getattr(shop, 'trial_ends', None)
    if end is None:
        return None
    return max((end - today).days, 0)


def shop_note(request):
    """A one-line notice for the top of every page of a shop: trial days left, or that the shop is locked. '' otherwise."""
    shop = getattr(request, 'tenant', None)
    if shop is None or getattr(shop, 'schema_name', 'public') == getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public'):
        return ''
    state = shop_state(shop)
    if state == 'locked':
        return ('Your subscription has ended. You can still view your data, but new entries are switched off. '
                'Please contact us to continue.')
    left = days_left(shop)
    if state == 'trial' and left is not None:
        if left <= 7:
            return f"Your free trial ends in {left} day{'s' if left != 1 else ''}. Please contact us to choose a plan."
        return f'Free trial: {left} days left.'
    if state == 'active' and left is not None and left <= 7:
        return f"Your plan ends in {left} day{'s' if left != 1 else ''}. Please renew to avoid interruption."
    return ''


def clean_site_form(post):
    """Checks the 'website settings' form of the platform panel. Returns (values, errors). A blank price means
    'use the standard price from the settings file'; a blank number, e-mail or notice simply hides that item."""
    errors = []
    number = re.sub(r'[\s\-+()]', '', (post.get('whatsapp') or ''))
    if number and not (number.isdigit() and 10 <= len(number) <= 15):
        errors.append('The WhatsApp number must have 10 to 15 digits, with the country code (for example 919876543210).')
    email = (post.get('contact_email') or '').strip()
    if email:
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email
        try:
            validate_email(email)
        except ValidationError:
            errors.append('The contact e-mail is not valid.')
    notice = ' '.join((post.get('announcement') or '').split())
    if len(notice) > 200:
        errors.append('The announcement can have at most 200 characters.')
    prices = {}
    for key, label in (('price_monthly', 'monthly'), ('price_yearly', 'yearly')):
        raw = (post.get(key) or '').strip().replace(',', '')
        if not raw:
            prices[key] = None
        elif raw.isdigit() and 100 <= int(raw) <= 100000:
            prices[key] = int(raw)
        else:
            prices[key] = None
            errors.append(f'The {label} price must be a whole number of rupees between 100 and 1,00,000.')
    return dict(whatsapp=number, contact_email=email, announcement=notice, **prices), errors


def is_unknown_shop_host(host, base):
    """True for an address such as nobody.<base> (a shop address): below the base domain, but not the base itself
    and not www. Used to answer 'shop not found' instead of showing the main site under a made-up name."""
    host = (host or '').split(':')[0].strip().lower().rstrip('.')
    base = (base or '').strip().lower()
    if not base or host in (base, 'www.' + base):
        return False
    return host.endswith('.' + base)


def on_shop_site():
    """True while a shop's own address is open (never on the main site, never when many shops is off)."""
    return bool(getattr(settings, 'MULTI_SHOP', False)) and not is_platform()


REMINDER_AFTER_DAYS = 14        # a 'plan has ended' reminder is sent once, only within this many days after the end


def reminder_due(shop, today=None):
    """Which reminder a shop should get today: ('trial7' | 'trial1' | 'trial_end' | 'paid7' | 'paid1' | 'paid_end', end_date),
    or None. 7 means 2 to 7 days left, 1 means the last 2 days (1 day left or the last day itself)."""
    from django.utils import timezone
    today = today or timezone.localdate()
    if getattr(shop, 'schema_name', 'public') == getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public') or getattr(shop, 'is_paused', False):
        return None
    on_trial = getattr(shop, 'plan', 'trial') == 'trial'
    end = getattr(shop, 'trial_ends', None) if on_trial else getattr(shop, 'paid_until', None)
    if end is None:
        return None
    prefix = 'trial' if on_trial else 'paid'
    state = shop_state(shop, today)
    left = (end - today).days
    if state in ('trial', 'active'):
        if left <= 1:
            return prefix + '1', end
        if left <= 7:
            return prefix + '7', end
        return None
    if state == 'locked' and 1 <= -left <= REMINDER_AFTER_DAYS:
        return prefix + '_end', end
    return None


def reminder_key(kind, end):
    """Remembered on the shop so the same reminder is never sent twice; a new end date (renewal) starts afresh."""
    return f'{kind}:{end.isoformat()}'


def port_suffix(request):
    """':8000' when the site is opened on an unusual port while testing on a computer, else ''.

    Behind the web server the port the app itself listens on (like 8091) must never go into an address given to a person,
    so on a secure (https) site the port is never added."""
    if request.is_secure():
        return ''
    port = request.get_port()
    return f':{port}' if port not in ('80', '443', '') else ''


def platform_panel_url(user):
    """'/superadmin/' for a platform admin on the main site (so every page can link back to the panel), else ''."""
    from django.conf import settings
    if not getattr(settings, 'MULTI_SHOP', False) or not is_platform():
        return ''
    try:
        from tenants.platform import is_platform_admin
        return '/superadmin/' if is_platform_admin(user) else ''
    except Exception:
        return ''
