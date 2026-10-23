"""What the public website shows that the platform owner can edit (see SiteSetting). Read with a 60-second cache and
never raises: if the table cannot be read, the standard values from the settings file are used."""
import logging

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)
KEY = 'site-setting-v1'
FIELDS = ('whatsapp', 'contact_email', 'announcement', 'price_monthly', 'price_yearly')


def current():
    """A dict with the five editable values (blank or None when not set)."""
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


def prices():
    """{'monthly': rupees, 'yearly': rupees}: the edited price when one is set, else the standard one."""
    data = current()
    return {'monthly': data.get('price_monthly') or settings.PLAN_PRICES['monthly'],
            'yearly': data.get('price_yearly') or settings.PLAN_PRICES['yearly']}
