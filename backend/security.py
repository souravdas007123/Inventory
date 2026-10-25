"""Blocks repeated wrong-password attempts at login (brute force)."""
import hashlib
import os

from django.contrib.auth.views import LoginView
from django.core.cache import cache

MAX_ATTEMPTS = 5
LOCK_SECONDS = 15 * 60


def client_ip(request):
    if os.environ.get('DJANGO_BEHIND_PROXY', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if fwd:
            return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class ThrottledLoginView(LoginView):
    """Login is blocked for 15 minutes (per IP + username) after 5 failed attempts."""
    template_name = 'app/login.html'

    def dispatch(self, request, *args, **kwargs):
        from django.conf import settings
        if getattr(settings, 'MULTI_SHOP', False):
            from . import tenancy
            if tenancy.is_platform():                # the main site: one sign-in page for every shop (see tenants/mainlogin.py)
                from tenants.mainlogin import main_login
                return main_login(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """On a shop's own address 'Back to home page' goes to the main site (the shop's own '/' is its private dashboard)."""
        ctx = super().get_context_data(**kwargs)
        from django.conf import settings
        if getattr(settings, 'MULTI_SHOP', False):
            from . import tenancy
            if not tenancy.is_platform():
                ctx['home_url'] = f"{self.request.scheme}://{settings.SHOP_BASE_DOMAIN}{tenancy.port_suffix(self.request)}/"
        return ctx

    def _key(self):
        user = (self.request.POST.get('username') or '').strip().lower()
        raw = f"{client_ip(self.request)}|{user}"
        return 'login-fail:' + hashlib.sha256(raw.encode()).hexdigest()

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= MAX_ATTEMPTS:
            form = self.get_form_class()(request=request)          # unbound: will not authenticate again
            resp = self.render_to_response(self.get_context_data(form=form, locked=True))
            resp.status_code = 429
            return resp
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        cache.set(key, cache.get(key, 0) + 1, LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)
