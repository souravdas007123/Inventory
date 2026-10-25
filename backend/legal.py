"""Terms of use, privacy policy and refund policy: plain public pages on the main site (a shop's own address has none)."""
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import tenancy
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
