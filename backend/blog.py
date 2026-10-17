"""The public blog: /blog/ (list) and /blog/<slug>/ (one article). Articles are written in the admin.

The article text is plain text with a few simple marks (see render_body). Everything is escaped first, so an article
can never inject HTML or scripts into the page.
"""
import json
import re

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from . import access
from .blog_art import cover_svg
from .landing import _site_url, cta_context
from .models import BlogPost

_BOLD = re.compile(r'\*\*(.+?)\*\*')
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^\s)]+|/[^\s)]*)\)')
_OL = re.compile(r'^\d+[.)]\s+(.*)$')
_IMG = re.compile(r'^!\[([^\]\n]*)\]\((https://[^\s)]+|/(?!/)[^\s)]*)\)$')


def _inline(text):
    out = escape(text)
    out = _BOLD.sub(r'<strong>\1</strong>', out)

    def link(m):
        url = m.group(2)
        extra = ' rel="noopener" target="_blank"' if url.startswith('http') else ''
        return f'<a href="{url}"{extra}>{m.group(1)}</a>'
    return _LINK.sub(link, out)


def render_body(text):
    """Plain text -> safe HTML. '## ' / '### ' headings, '- ' bullets, '1. ' numbers, blank line = new paragraph."""
    html, para, items, kind = [], [], [], None

    def flush_para():
        if para:
            html.append('<p>' + _inline(' '.join(para)) + '</p>')
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            tag = 'ol' if kind == 'ol' else 'ul'
            html.append(f'<{tag}>' + ''.join(f'<li>{_inline(i)}</li>' for i in items) + f'</{tag}>')
            items.clear()
        kind = None

    for raw in (text or '').replace('\r\n', '\n').split('\n'):
        line = raw.strip()
        ol = _OL.match(line)
        img = _IMG.match(line)
        if not line:
            flush_para()
            flush_list()
        elif img:
            flush_para(); flush_list()
            html.append(format_html('<figure><img src="{}" alt="{}" loading="lazy" decoding="async"></figure>', img.group(2), img.group(1)))
        elif line.startswith('### '):
            flush_para(); flush_list()
            html.append('<h3>' + _inline(line[4:]) + '</h3>')
        elif line.startswith('## '):
            flush_para(); flush_list()
            html.append('<h2>' + _inline(line[3:]) + '</h2>')
        elif line.startswith('- '):
            flush_para()
            if kind != 'ul':
                flush_list()
            kind = 'ul'
            items.append(line[2:])
        elif ol:
            flush_para()
            if kind != 'ol':
                flush_list()
            kind = 'ol'
            items.append(ol.group(1))
        else:
            flush_list()
            para.append(line)
    flush_para()
    flush_list()
    return mark_safe(''.join(html))


def reading_minutes(text):
    return max(1, round(len((text or '').split()) / 200))


def _jsonld(data):
    """JSON for a <script type="application/ld+json"> tag. '<' is escaped so the text can never close the tag."""
    return mark_safe(json.dumps(data, ensure_ascii=False).replace('<', '\\u003c'))


def post_image(post, decorative=False, lazy=True):
    """The picture at the top of an article: its own picture if one is set, else the chosen illustration, else nothing."""
    url = (post.image_url or '').strip()
    if url and (url.startswith('https://') or (url.startswith('/') and not url.startswith('//'))):
        return format_html('<img src="{}" alt="{}" {} decoding="async">', url, '' if decorative else (post.image_alt or post.title),
                           mark_safe('loading="lazy"') if lazy else '')
    return cover_svg(post.cover, decorative=decorative)


def _absolute(site, url):
    return url if url.startswith('http') else site + url


def _og_image(site, post):
    url = (post.image_url or '').strip()
    return _absolute(site, url) if url and (url.startswith('https://') or (url.startswith('/') and not url.startswith('//'))) else f'{site}/og.png'


def _card(p):
    return dict(title=p.title, summary=p.summary, url=p.get_absolute_url(), date=p.published_at, minutes=reading_minutes(p.body),
                image=post_image(p, decorative=True))


@require_GET
def blog_list(request):
    site = _site_url(request)
    posts = [_card(p) for p in BlogPost.live()[:100]]
    ld = {'@context': 'https://schema.org', '@type': 'Blog', 'name': 'MedSync Blog', 'url': f'{site}/blog/',
          'description': 'Practical articles for medical store owners: expiry, GST, drug registers, suppliers and stock.',
          'blogPost': [{'@type': 'BlogPosting', 'headline': p['title'], 'url': site + p['url'],
                        'datePublished': p['date'].isoformat()} for p in posts]}
    return render(request, 'app/blog_list.html', dict(site_url=site, posts=posts, jsonld=_jsonld(ld), **cta_context(request)))


@require_GET
def blog_post(request, slug):
    post = BlogPost.objects.filter(slug=slug).first()
    if post is None:
        raise Http404
    preview = False
    if not (post.published and post.published_at <= timezone.now()):
        if not access.is_owner(request.user):          # drafts and future-dated articles: only the owner may preview them
            raise Http404
        preview = True
    site = _site_url(request)
    more = [_card(p) for p in BlogPost.live().exclude(pk=post.pk)[:3]]
    ld = {'@context': 'https://schema.org', '@type': 'BlogPosting', 'headline': post.title, 'description': post.summary,
          'datePublished': post.published_at.isoformat(), 'dateModified': post.updated_at.isoformat(),
          'mainEntityOfPage': f'{site}{post.get_absolute_url()}', 'image': _og_image(site, post),
          'author': {'@type': 'Organization', 'name': 'MedSync'}, 'publisher': {'@type': 'Organization', 'name': 'MedSync'},
          'inLanguage': 'en'}
    return render(request, 'app/blog_post.html', dict(
        site_url=site, post=post, body=render_body(post.body), cover=post_image(post, lazy=False), og_image=_og_image(site, post), minutes=reading_minutes(post.body), more=more,
        preview=preview, jsonld=_jsonld(ld), **cta_context(request)))
