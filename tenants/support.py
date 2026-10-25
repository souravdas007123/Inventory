"""Support tickets, the shop's side: a shop writes to the platform owner from inside the app and reads the replies.
(The platform owner's side is in supportadmin.py.) Only what the shop itself types is ever sent."""
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from backend import tenancy

from .models import Domain, Ticket, TicketMessage

log = logging.getLogger(__name__)
MAX_NEW_PER_HOUR = 5
MAX_MESSAGES_PER_HOUR = 20
CATEGORY_KEYS = [k for k, _ in Ticket.CATEGORIES]


def clean_message(body):
    body = (body or '').replace('\r\n', '\n').strip()
    if not body:
        return body, 'Please write your message.'
    if len(body) > 4000:
        return body, 'The message can have at most 4000 characters.'
    return body, ''


def clean_ticket(post):
    """Checks the 'new ticket' form. Returns (values, errors)."""
    errors = []
    subject = ' '.join((post.get('subject') or '').split())
    if len(subject) < 4:
        errors.append('Please write a short subject (at least 4 characters).')
    elif len(subject) > 120:
        errors.append('The subject can have at most 120 characters.')
    category = post.get('category') or 'other'
    if category not in CATEGORY_KEYS:
        errors.append('Please choose a category from the list.')
    body, err = clean_message(post.get('body'))
    if err:
        errors.append(err)
    return dict(subject=subject, category=category, body=body), errors


def page_from(request):
    """The page the person was on before opening support (from the Referer header), or ''. Never an outside address."""
    ref = request.META.get('HTTP_REFERER') or request.POST.get('page') or request.GET.get('from') or ''
    try:
        u = urlparse(ref)
    except ValueError:
        return ''
    if u.netloc and u.netloc != request.get_host():
        return ''
    path = u.path or ''
    if not path.startswith('/') or path.startswith('//') or path.startswith('/support/') or len(path) > 200:
        return ''
    return path


def _limited(shop, kind, limit):
    key = f'support-{kind}:{shop.pk}'
    n = cache.get(key, 0)
    if n >= limit:
        return True
    cache.set(key, n + 1, 3600)
    return False


def platform_url(request, path):
    return f"{request.scheme}://{settings.SHOP_BASE_DOMAIN}{tenancy.port_suffix(request)}{path}"


def _send(subject, text, to):
    to = [a for a in to if a]
    if not to:
        return
    try:
        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
    except Exception:
        log.exception('Could not send the support e-mail "%s"', subject)


def mail_platform(request, ticket, message, new):
    from . import site
    to = list(getattr(settings, 'ALERT_EMAILS', []) or []) or [site.current().get('contact_email') or '']
    head = 'New support ticket' if new else 'New reply on support ticket'
    _send(f'[MedSync] {head} #{ticket.pk}: {ticket.subject}',
          f'Shop: {ticket.shop.name}\nFrom: {message.author}\nCategory: {ticket.get_category_display()}\n\n{message.body}\n\n'
          f'Open it: {platform_url(request, f"/superadmin/support/{ticket.pk}/")}\n', to)


def mail_shop(request, ticket, message):
    domain = Domain.objects.filter(tenant=ticket.shop).order_by('-is_primary', 'id').first()
    link = f"{request.scheme}://{domain.domain}{tenancy.port_suffix(request)}/support/{ticket.pk}/" if domain else ''
    _send(f'MedSync support replied to your ticket #{ticket.pk}',
          f'Hello,\n\nMedSync support has replied to "{ticket.subject}":\n\n{message.body}\n\n'
          + (f'Read and answer here: {link}\n' if link else '') + '\nMedSync', [ticket.shop.email])


def _shop(request):
    shop = getattr(request, 'tenant', None)
    if not tenancy.on_shop_site() or shop is None or shop.schema_name == settings.PUBLIC_SCHEMA_NAME:
        raise Http404
    return shop


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def ticket_list(request):
    shop = _shop(request)
    values = dict(subject='', category='other', body='')
    if request.method == 'POST':
        values, errors = clean_ticket(request.POST)
        if not errors and _limited(shop, 'new', MAX_NEW_PER_HOUR):
            errors.append('You have sent several tickets in the last hour. Please wait a little, or add to an existing ticket.')
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            ticket = Ticket.objects.create(shop=shop, subject=values['subject'], category=values['category'],
                                           created_by=request.user.get_username()[:150])
            msg = TicketMessage.objects.create(ticket=ticket, from_shop=True, author=request.user.get_username()[:150],
                                               body=values['body'], page=page_from(request))
            mail_platform(request, ticket, msg, new=True)
            messages.success(request, f'Your ticket #{ticket.pk} was sent. We will reply here, and by e-mail.')
            return redirect('support_ticket', pk=ticket.pk)
    return render(request, 'app/support.html', dict(
        active='support', tickets=Ticket.objects.filter(shop=shop)[:50], values=values, categories=Ticket.CATEGORIES,
        page=page_from(request)))


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def ticket_detail(request, pk):
    shop = _shop(request)
    ticket = get_object_or_404(Ticket, pk=pk, shop=shop)          # a shop can open only its own tickets
    if request.method == 'POST':
        body, err = clean_message(request.POST.get('body'))
        if not err and _limited(shop, 'msg', MAX_MESSAGES_PER_HOUR):
            err = 'You have sent many messages in the last hour. Please wait a little.'
        if err:
            messages.error(request, err)
        else:
            msg = TicketMessage.objects.create(ticket=ticket, from_shop=True, author=request.user.get_username()[:150],
                                               body=body, page=page_from(request))
            ticket.status = 'open'                              # a reply also reopens a closed ticket
            ticket.save()
            mail_platform(request, ticket, msg, new=False)
            messages.success(request, 'Your message was sent.')
            return redirect('support_ticket', pk=ticket.pk)
    elif ticket.shop_unread:
        Ticket.objects.filter(pk=ticket.pk).update(shop_unread=False)
    return render(request, 'app/support_ticket.html', dict(active='support', ticket=ticket, thread=ticket.messages.all()))
