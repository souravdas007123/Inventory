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
from django.utils.html import escape
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from . import views
from .models import BlogPost

ASSETS = Path(__file__).parent / 'landing_assets'


def _site_url(request):
    return f"{request.scheme}://{request.get_host()}"


def cta_context(request):
    """The 'Get a demo' button: a WhatsApp link when a number is configured, otherwise the login."""
    number = re.sub(r'\D', '', getattr(settings, 'LANDING_WHATSAPP', '') or '')
    if 10 <= len(number) <= 15:
        return dict(cta_url=f"https://wa.me/{number}?text={quote('Hi, I want a MedSync demo')}",
                    cta_nav='Get a demo', cta_hero='Ask for a free demo', cta_final='Message us on WhatsApp')
    return dict(cta_url='/login/', cta_nav='Login', cta_hero='Login to MedSync', cta_final='Login to MedSync')


def home(request):
    if request.user.is_authenticated:
        return views.dashboard(request)
    try:                                            # the blog teaser must never be able to break the home page
        latest = [dict(title=p.title, summary=p.summary, url=p.get_absolute_url(), date=p.published_at)
                  for p in BlogPost.live()[:3]]
    except Exception:
        latest = []
    return render(request, 'app/landing.html', dict(site_url=_site_url(request), latest_posts=latest, **cta_context(request)))


@require_GET
def robots_txt(request):
    body = f"User-agent: *\nAllow: /$\nAllow: /blog/\nDisallow: /\n\nSitemap: {_site_url(request)}/sitemap.xml\n"
    return HttpResponse(body, content_type='text/plain')


@require_GET
def sitemap_xml(request):
    base = _site_url(request)
    urls = [f'  <url><loc>{base}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>',
            f'  <url><loc>{base}/blog/</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>']
    try:
        for p in BlogPost.live():
            urls.append(f'  <url><loc>{base}{escape(p.get_absolute_url())}</loc><lastmod>{p.updated_at:%Y-%m-%d}</lastmod>'
                        f'<changefreq>monthly</changefreq><priority>0.7</priority></url>')
    except Exception:
        pass
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + '\n'.join(urls) + '\n</urlset>\n'
    return HttpResponse(body, content_type='application/xml')


@require_GET
@cache_control(public=True, max_age=86400)
def asset(request, name):
    types = {'og.png': 'image/png', 'favicon.svg': 'image/svg+xml'}
    if name not in types or not (ASSETS / name).exists():
        raise Http404
    return FileResponse(open(ASSETS / name, 'rb'), content_type=types[name])
