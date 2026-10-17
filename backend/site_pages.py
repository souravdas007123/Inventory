"""Public pages: /pricing/ (plans from the admin) and /terms/, /privacy/, /refund/ (text pages from the admin)."""
import re
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .blog import _jsonld, render_body
from .landing import _site_url, cta_context
from .models import Plan, SitePage

LEGAL = {'terms': '/terms/', 'privacy': '/privacy/', 'refund': '/refund/'}

FAQ = [
    ('Can I try MedSync before I pay?', 'Yes. Ask for a demo and we will show you MedSync with sample data, then help you try it with your own products.'),
    ('Is my data safe?', 'Your shop data is kept in its own database, a backup is taken every day, and you can export everything to Excel at any time.'),
    ('Can you move my existing stock from Excel?', 'Yes. MedSync can import products and stock from an Excel file, and we can help you set it up.'),
    ('Can I change my plan later?', 'Yes. Tell us and we will move you to another plan.'),
]


def rupees(value):
    """12500 -> '12,500' (Indian digit grouping); whole rupees without '.00', otherwise two decimals."""
    if value is None:
        return ''
    value = Decimal(value)
    whole = value == value.to_integral_value()
    text = f'{abs(value):.0f}' if whole else f'{abs(value):.2f}'
    head, _, tail = text.partition('.')
    if len(head) > 3:
        rest, last3 = head[:-3], head[-3:]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        head = ','.join(parts + [last3])
    return ('-' if value < 0 else '') + head + ('.' + tail if tail else '')


def _plan_cta(plan_name):
    number = re.sub(r'\D', '', getattr(settings, 'LANDING_WHATSAPP', '') or '')
    if 10 <= len(number) <= 15:
        return f"https://wa.me/{number}?text={quote('Hi, I am interested in the ' + plan_name + ' plan of MedSync')}", 'Ask about this plan'
    return '/login/', 'Login'


def _plan_card(plan):
    url, label = _plan_cta(plan.name)
    return dict(name=plan.name, tagline=plan.tagline, features=plan.feature_list(), highlight=plan.highlight,
                monthly=rupees(plan.monthly_price) if plan.monthly_price is not None else '',
                yearly=rupees(plan.yearly_price) if plan.yearly_price is not None else '',
                setup=rupees(plan.setup_fee) if plan.setup_fee is not None else '',
                has_price=plan.monthly_price is not None or plan.yearly_price is not None,
                saving=plan.yearly_saving_percent(), note=plan.price_note, cta_url=url, cta_label=label)


@require_GET
def pricing(request):
    site = _site_url(request)
    try:
        plans = [_plan_card(p) for p in Plan.objects.filter(is_active=True)]
    except Exception:
        plans = []
    included = SitePage.objects.filter(slug='pricing-included').first()
    ld = {'@context': 'https://schema.org', '@type': 'FAQPage',
          'mainEntity': [{'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in FAQ]}
    return render(request, 'app/pricing.html', dict(
        site_url=site, plans=plans, included=render_body(included.body) if included else '',
        included_title=included.title if included else '', faq=FAQ, jsonld=_jsonld(ld), **cta_context(request)))


@require_GET
def legal(request, slug):
    page = SitePage.objects.filter(slug=slug).first() if slug in LEGAL else None
    if page is None:
        raise Http404
    return render(request, 'app/legal.html', dict(site_url=_site_url(request), page=page, path=LEGAL[slug],
                                                  body=render_body(page.body), **cta_context(request)))
