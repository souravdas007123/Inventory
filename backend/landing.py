"""The public home page (shown at "/" to visitors who are not logged in) and its SEO files.

Logged-in users still get the dashboard at "/". The page is a plain template with no database access,
so it is fast and cannot break the app.
"""
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from . import views

ASSETS = Path(__file__).parent / 'landing_assets'


def _site_url(request):
    return f"{request.scheme}://{request.get_host()}"


def home(request):
    if request.user.is_authenticated:
        return views.dashboard(request)
    number = re.sub(r'\D', '', getattr(settings, 'LANDING_WHATSAPP', '') or '')
    if 10 <= len(number) <= 15:
        cta = dict(cta_url=f"https://wa.me/{number}?text={quote('Hi, I want a MedSync demo')}",
                   cta_nav='Get a demo', cta_hero='Ask for a free demo', cta_final='Message us on WhatsApp')
    else:                                           # no WhatsApp number configured: the buttons lead to the login
        cta = dict(cta_url='/login/', cta_nav='Login', cta_hero='Login to MedSync', cta_final='Login to MedSync')
    return render(request, 'app/landing.html', dict(site_url=_site_url(request), **cta))


@require_GET
def robots_txt(request):
    body = f"User-agent: *\nAllow: /$\nDisallow: /\n\nSitemap: {_site_url(request)}/sitemap.xml\n"
    return HttpResponse(body, content_type='text/plain')


@require_GET
def sitemap_xml(request):
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{_site_url(request)}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>\n</urlset>\n')
    return HttpResponse(body, content_type='application/xml')


@require_GET
@cache_control(public=True, max_age=86400)
def asset(request, name):
    types = {'og.png': 'image/png', 'favicon.svg': 'image/svg+xml'}
    if name not in types or not (ASSETS / name).exists():
        raise Http404
    return FileResponse(open(ASSETS / name, 'rb'), content_type=types[name])
