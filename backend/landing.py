"""The public home page (shown at "/" to visitors who are not logged in) and its SEO files.

Logged-in users still get the dashboard at "/". The page is a plain template with no database access,
so it is fast and cannot break the app.
"""
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import escape
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from . import tenancy, views
from .blog_art import post_image
from .models import BlogPost

ASSETS = Path(__file__).parent / 'landing_assets'


def _site_url(request):
    return f"{request.scheme}://{request.get_host()}"


def site_context():
    """Extras for the home page when many shops is on: notice bar, prices and contact links, all editable by the
    platform owner. Never raises, so the home page cannot be broken by it."""
    try:
        from tenants import site
        data, price = site.current(), site.prices()
        number = re.sub(r'\D', '', data.get('whatsapp') or getattr(settings, 'LANDING_WHATSAPP', '') or '')
        saving = price['monthly'] * 12 - price['yearly']
        return dict(pricing=True, price_monthly=price['monthly'], price_yearly=price['yearly'],
                    price_saving=saving if saving > 0 else 0, trial_days=settings.TRIAL_DAYS,
                    site_notice=data.get('announcement') or '', contact_email=data.get('contact_email') or '',
                    contact_whatsapp=f"https://wa.me/{number}?text={quote('Hi, I have a question about MedSync')}"
                    if 10 <= len(number) <= 15 else '')
    except Exception:
        return {}


def cta_context(request):
    """The 'Get a demo' button: a WhatsApp link when a number is configured, otherwise the login."""
    if getattr(settings, 'MULTI_SHOP', False):         # many shops: visitors can open a shop themselves
        return dict(cta_url='/signup/', cta_nav='Free trial', cta_hero='Start your free trial', cta_final='Create your shop')
    number = re.sub(r'\D', '', getattr(settings, 'LANDING_WHATSAPP', '') or '')
    if 10 <= len(number) <= 15:
        return dict(cta_url=f"https://wa.me/{number}?text={quote('Hi, I want a MedSync demo')}",
                    cta_nav='Get a demo', cta_hero='Ask for a free demo', cta_final='Message us on WhatsApp')
    return dict(cta_url='/login/', cta_nav='Login', cta_hero='Login to MedSync', cta_final='Login and start billing')


def home(request):
    if request.user.is_authenticated:
        return views.dashboard(request)
    if not tenancy.is_platform():                   # a shop's own address: straight to its login, no marketing page
        return redirect('login')
    try:                                            # the blog teaser must never be able to break the home page
        latest = [dict(title=p.title, summary=p.summary, url=p.get_absolute_url(), date=p.published_at,
                       image=post_image(p, decorative=True))
                  for p in BlogPost.live()[:3]]
    except Exception:
        latest = []
    return render(request, 'app/landing.html', dict(site_url=_site_url(request), latest_posts=latest, **cta_context(request),
                                                         **(site_context() if getattr(settings, 'MULTI_SHOP', False) else {})))


@require_GET
def robots_txt(request):
    if not tenancy.is_platform():
        return HttpResponse('User-agent: *\nDisallow: /\n', content_type='text/plain')
    body = f"User-agent: *\nAllow: /$\nAllow: /blog/\nAllow: /favicon.ico\nAllow: /favicon.svg\nAllow: /apple-touch-icon.png\nAllow: /og.png\nDisallow: /\n\nSitemap: {_site_url(request)}/sitemap.xml\n"
    return HttpResponse(body, content_type='text/plain')


@require_GET
def sitemap_xml(request):
    if not tenancy.is_platform():
        raise Http404
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
    types = {'og.png': 'image/png', 'favicon.svg': 'image/svg+xml', 'apple-touch-icon.png': 'image/png', 'favicon.ico': 'image/x-icon'}
    if name not in types or not (ASSETS / name).exists():
        raise Http404
    return FileResponse(open(ASSETS / name, 'rb'), content_type=types[name])
