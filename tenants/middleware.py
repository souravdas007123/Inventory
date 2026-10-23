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
        if (shop is None or shop.schema_name == getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')) \
                and tenancy.is_unknown_shop_host(request.get_host(), getattr(settings, 'SHOP_BASE_DOMAIN', '')):
            return self._page('Shop not found', 'There is no MedSync shop at this address. Please check the web address '
                              'you were given, or open the main site to create your own shop.', 404,
                              link_url=self._main_site(request), link_text='Open MedSync')
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
    def _main_site(request):
        port = request.get_port()
        extra = f':{port}' if port not in ('80', '443') else ''
        return f"{request.scheme}://{settings.SHOP_BASE_DOMAIN}{extra}/"

    @staticmethod
    def _page(title, message, status, link_url='/', link_text='Back'):
        html = render_to_string('app/notice.html', {'title': title, 'message': message, 'link_url': link_url, 'link_text': link_text})
        return HttpResponse(html, status=status)
