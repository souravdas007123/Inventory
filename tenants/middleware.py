"""Switches a shop off (paused) or makes it read-only (trial or plan ended). Runs right after the shop is found.

It answers with a plain page built without the normal page context, because at this point the user is not loaded yet."""
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string

from backend import tenancy

ALWAYS_OPEN = ('/login/', '/logout/')          # a locked shop's owner must still be able to sign in and out


class ShopAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        shop = getattr(request, 'tenant', None)
        if shop is not None and getattr(shop, 'schema_name', 'public') != getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public'):
            state = tenancy.shop_state(shop)
            if state == 'paused':
                return self._page('This shop is switched off', 'Please contact MedSync support to switch it on again.', 403)
            if state == 'locked' and request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path not in ALWAYS_OPEN:
                return self._page('Your subscription has ended',
                                  'You can still view your data, but saving new entries is switched off. '
                                  'Please contact MedSync to choose a plan and continue.', 403)
        return self.get_response(request)

    @staticmethod
    def _page(title, message, status):
        html = render_to_string('app/notice.html', {'title': title, 'message': message, 'link_url': '/', 'link_text': 'Back'})
        return HttpResponse(html, status=status)
