"""What the public website shows that the platform owner can edit (see SiteSetting). Read with a 60-second cache and
never raises: if the table cannot be read, the standard values from the settings file are used."""
import logging

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)
KEY = 'site-setting-v1'
FIELDS = ('whatsapp', 'contact_email', 'announcement', 'shop_notice', 'price_monthly', 'price_yearly', 'upi_id', 'upi_name')


def current():
    """A dict with the editable values (blank or None when not set)."""
    data = cache.get(KEY)
    if data is None:
        data = dict.fromkeys(FIELDS)
        try:
            from .models import SiteSetting
            row = SiteSetting.objects.filter(pk=1).first()
            if row:
                data = {f: getattr(row, f) for f in FIELDS}
        except Exception:
            log.exception('Could not read the website settings')
        cache.set(KEY, data, 60)
    return data


def save(values):
    from .models import SiteSetting
    SiteSetting.objects.update_or_create(pk=1, defaults=values)
    cache.delete(KEY)


def shop_notice():
    """The platform-wide message shown inside every shop ('' when none or when it cannot be read)."""
    try:
        return current().get('shop_notice') or ''
    except Exception:
        return ''


def prices():
    """{'monthly': rupees, 'yearly': rupees}: the edited price when one is set, else the standard one."""
    data = current()
    return {'monthly': data.get('price_monthly') or settings.PLAN_PRICES['monthly'],
            'yearly': data.get('price_yearly') or settings.PLAN_PRICES['yearly']}


def upi():
    """(upi id, name shown to the payer) for the UPI payment, or None when no UPI id is set."""
    data = current()
    upi_id = (data.get('upi_id') or '').strip()
    return (upi_id, (data.get('upi_name') or '').strip() or 'MedSync') if upi_id else None
