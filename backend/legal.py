"""Terms of use, privacy policy and refund policy: plain public pages on the main site (a shop's own address has none)."""
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import sitecontent, tenancy
from .blog import render_body
from .landing import site_context

UPDATED = 'October 2026'
PAGES = {
    'terms': ('Terms of use', 'app/legal_terms.html'),
    'privacy': ('Privacy policy', 'app/legal_privacy.html'),
    'refund': ('Refund and cancellation', 'app/legal_refund.html'),
}


def _page(request, key):
    if not tenancy.is_platform():
        raise Http404
    title, template = PAGES[key]
    ctx = site_context() if getattr(settings, 'MULTI_SHOP', False) else {}
    saved = sitecontent.legal_page(key)         # the text the owner edited in the admin; the built-in page is the fallback
    if saved:
        description, body, on = saved
        return render(request, 'app/legal_saved.html', dict(title=title, updated=f'{on:%d %B %Y}'.lstrip('0'), pages=PAGES, key=key,
                                                            description=description, body=render_body(body), **ctx))
    return render(request, template, dict(title=title, updated=UPDATED, pages=PAGES, key=key, **ctx))


@require_GET
def terms(request):
    return _page(request, 'terms')


@require_GET
def privacy(request):
    return _page(request, 'privacy')


@require_GET
def refund(request):
    return _page(request, 'refund')
