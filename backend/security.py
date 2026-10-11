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
