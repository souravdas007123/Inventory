"""Switches a shop off (paused) or makes it read-only (trial or plan ended). Runs right after the shop is found.

It answers with a plain page built without the normal page context, because at this point the user is not loaded yet."""
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string

from backend import tenancy

from . import maintenance

ALWAYS_OPEN = ('/login/', '/login/verify/', '/logout/', '/forgot/')       # a locked shop's owner must still be able to sign in and out
ALWAYS_OPEN_PREFIX = '/reset-password/'                  # and to choose a new password


ALWAYS_OPEN_SUPPORT = '/support/'                          # a shop whose plan ended must still be able to write to us


MAINTENANCE_OPEN = ('/healthz/', '/sw.js', '/manifest.webmanifest')      # a monitor and the phone app files keep working


def is_always_open(path):
    return path in ALWAYS_OPEN or path.startswith(ALWAYS_OPEN_PREFIX) or path.startswith(ALWAYS_OPEN_SUPPORT)


class ShopAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        shop = getattr(request, 'tenant', None)
        if (shop is None or shop.schema_name == getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')) \
                and tenancy.is_unknown_shop_host(request.get_host(), getattr(settings, 'SHOP_BASE_DOMAIN', '')):
            return self._page('Shop not found', 'There is no MedSync shop at this address. Please check the web address '
                              'you were given, or open the main site to create your own shop.', 404,
                              link_url=self._main_site(request), link_text='Open MedSync')
        if shop is not None and getattr(shop, 'schema_name', 'public') != getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public'):
            if maintenance.is_active() and request.path not in MAINTENANCE_OPEN and not request.path.startswith('/icon-'):
                return self._maintenance()
            state = tenancy.shop_state(shop)
            if state == 'paused':
                return self._page('This shop is switched off', 'Please contact MedSync support to switch it on again.', 403)
            if state == 'locked' and request.method not in ('GET', 'HEAD', 'OPTIONS') and not is_always_open(request.path):
                return self._page('Your subscription has ended',
                                  'You can still view your data, but saving new entries is switched off. '
                                  'Please contact MedSync to choose a plan and continue.', 403)
        return self.get_response(request)

    @staticmethod
    def _contact():
        try:
            from .reminders import _contact_lines
            return [c for c in _contact_lines() if not c.startswith('Please contact')]
        except Exception:
            return []

    def _maintenance(self):
        """A plain 503 page. It needs no database, session or user, so it works while the update is running."""
        try:
            kind, data = maintenance.state()
            until = maintenance.fmt(data['end']) if data else ''
            note = data['message'] if data else ''
            wait = maintenance.retry_after()
        except Exception:
            until, note, wait = '', '', 60
        html = render_to_string('app/maintenance.html', {'until': until, 'note': note, 'contact': self._contact()})
        response = HttpResponse(html, status=503)
        response['Retry-After'] = str(wait)
        response['Cache-Control'] = 'no-store'
        return response

    @staticmethod
    def _main_site(request):
        return f"{request.scheme}://{settings.SHOP_BASE_DOMAIN}{tenancy.port_suffix(request)}/"

    @staticmethod
    def _page(title, message, status, link_url='/', link_text='Back'):
        html = render_to_string('app/notice.html', {'title': title, 'message': message, 'link_url': link_url, 'link_text': link_text})
        return HttpResponse(html, status=status)
