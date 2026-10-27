"""Sign-up for a new shop (only on the main site): form, email confirmation, then the shop is made.

Nothing is created until the owner clicks the link in his email, so a mistyped or fake address never makes a schema."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from backend import tenancy
from backend import validators as vcheck

from . import mails, site
from .models import Domain, PendingSignup, Shop

log = logging.getLogger(__name__)
MAX_SIGNUPS_PER_HOUR = 5


def _notice(request, title, message, link_url='', link_text='', status=200):
    return render(request, 'app/notice.html', dict(title=title, message=message, link_url=link_url, link_text=link_text),
                  status=status)


def _client_ip(request):
    from backend.security import client_ip
    return client_ip(request)


def shop_url(request, subdomain, path='/'):
    """Full address of a shop, with the port when the site runs on a non-standard one (local testing)."""
    host = request.get_host()
    port = host.split(':', 1)[1] if ':' in host else ''
    return f"{request.scheme}://{subdomain}.{settings.SHOP_BASE_DOMAIN}{':' + port if port else ''}{path}"


def validate_signup(data):
    """(clean values, {field: message}). Pure checks plus database look-ups for names already taken."""
    errors = {}
    shop_name = ' '.join((data.get('shop_name') or '').split())
    owner_name = ' '.join((data.get('owner_name') or '').split())
    email = (data.get('email') or '').strip().lower()
    phone = vcheck.normalise_phone(data.get('phone'))
    password, again = data.get('password') or '', data.get('password2') or ''
    sub, sub_msg = tenancy.clean_subdomain(data.get('subdomain'))

    if not 2 <= len(shop_name) <= 120:
        errors['shop_name'] = 'Enter the name of your shop.'
    if not 2 <= len(owner_name) <= 120:
        errors['owner_name'] = 'Enter your name.'
    try:
        validate_email(email)
    except ValidationError:
        errors['email'] = 'Enter a valid email address.'
    if not phone:
        errors['phone'] = 'Enter your mobile number.'
    elif vcheck.phone_problem(phone):
        errors['phone'] = vcheck.phone_problem(phone)
    if sub_msg:
        errors['subdomain'] = sub_msg
    if not password:
        errors['password'] = 'Choose a password.'
    elif password != again:
        errors['password2'] = 'The two passwords do not match.'
    else:
        try:
            validate_password(password)
        except ValidationError as e:
            errors['password'] = ' '.join(e.messages)
    if not data.get('agree'):
        errors['agree'] = 'Please tick the box to continue.'

    if sub and 'subdomain' not in errors:
        domain = f'{sub}.{settings.SHOP_BASE_DOMAIN}'
        if Domain.objects.filter(domain=domain).exists() or Shop.objects.filter(schema_name=tenancy.schema_name_for(sub)).exists():
            errors['subdomain'] = 'This web address is already taken. Please choose another.'
        else:
            for p in PendingSignup.objects.filter(subdomain=sub, used=False):
                if not p.expired() and p.email != email:
                    errors['subdomain'] = 'This web address is already taken. Please choose another.'
    if email and 'email' not in errors and Shop.objects.filter(email__iexact=email).exists():
        errors['email'] = 'A shop already uses this email address. Please sign in, or contact us for a second shop.'
    clean = dict(shop_name=shop_name, owner_name=owner_name, email=email, phone=phone, subdomain=sub, password=password)
    return clean, errors


@require_http_methods(['GET', 'POST'])
def signup(request):
    if not tenancy.is_platform():
        raise Http404
    if request.user.is_authenticated:
        return redirect('/')
    values, errors = {}, {}
    if request.method == 'POST':
        values = request.POST
        if request.POST.get('website'):                       # hidden field that only robots fill in
            return redirect('/')
        key = 'signup-ip:' + _client_ip(request)
        if cache.get(key, 0) >= MAX_SIGNUPS_PER_HOUR:
            return _notice(request, 'Too many attempts', 'Please try again after an hour.', '/', 'Back to home page', 429)
        clean, errors = validate_signup(request.POST)
        if not errors:
            cache.set(key, cache.get(key, 0) + 1, 3600)
            PendingSignup.objects.filter(email=clean['email'], used=False).delete()      # an older unconfirmed try
            pending = PendingSignup.objects.create(
                token=tenancy.new_token(), subdomain=clean['subdomain'], shop_name=clean['shop_name'],
                owner_name=clean['owner_name'], email=clean['email'], phone=clean['phone'],
                password_hash=make_password(clean['password']))
            link = f"{request.scheme}://{request.get_host()}/signup/confirm/{pending.token}/"
            if getattr(settings, 'SIGNUP_SKIP_EMAIL', False):
                return redirect(link)
            try:
                subject, text, html = mails.confirm_message(pending, link, PendingSignup.VALID_HOURS)
                mails.send(subject, text, html, pending.email)
            except Exception:
                log.exception('Could not send the sign-up email')
                pending.delete()
                return _notice(request, 'We could not send the email',
                               'Please check the address and try again in a few minutes.', '/signup/', 'Back', 502)
            return _notice(request, 'Check your email',
                           f'We sent a link to {pending.email}. Open it to create your shop.')
    return render(request, 'app/signup.html', dict(values=values, errors=errors, trial_days=settings.TRIAL_DAYS,
                                                    base_domain=settings.SHOP_BASE_DOMAIN, prices=site.prices()))


def _create_shop(pending):
    """Makes the schema, the web address, the owner and the shop's first settings. Cleans up if anything fails."""
    from django_tenants.utils import schema_context

    from backend.models import ShopProfile
    schema = tenancy.schema_name_for(pending.subdomain)
    shop = Shop(schema_name=schema, name=pending.shop_name, owner_name=pending.owner_name, email=pending.email,
                phone=pending.phone, plan='trial', trial_ends=tenancy.trial_end(timezone.localdate()), email_verified=True)
    try:
        shop.save()                                           # makes the schema and all tables
        Domain.objects.create(domain=f'{pending.subdomain}.{settings.SHOP_BASE_DOMAIN}', tenant=shop, is_primary=True)
        with schema_context(schema):
            get_user_model().objects.create(
                username=pending.email, email=pending.email, first_name=pending.owner_name[:150],
                password=pending.password_hash, is_staff=True, is_superuser=True)
            ShopProfile.objects.create(name=pending.shop_name, email=pending.email, phone=pending.phone)
    except Exception:
        log.exception('Could not create the shop %s', schema)
        if shop.pk:
            try:
                shop.delete(force_drop=True)
            except Exception:
                log.exception('Could not clean up the shop %s', schema)
        raise
    return shop


@require_http_methods(['GET', 'POST'])
def confirm(request, token):
    if not tenancy.is_platform():
        raise Http404
    pending = PendingSignup.objects.filter(token=token).first()
    if pending is None or pending.expired():
        return _notice(request, 'This link is not valid',
                       'It may have expired. Please sign up again.', '/signup/', 'Sign up', 404)
    if pending.used:
        return _notice(request, 'Your shop is ready', 'This link was already used. You can sign in to your shop.',
                       shop_url(request, pending.subdomain, '/login/'), 'Go to my shop')
    if request.method == 'GET':                               # a button, so mail scanners that open links create nothing
        return render(request, 'app/signup_confirm.html', dict(pending=pending, trial_days=settings.TRIAL_DAYS))
    if not PendingSignup.objects.filter(pk=pending.pk, used=False).update(used=True):    # a double click: only one wins
        return _notice(request, 'Your shop is being created', 'Please wait a moment and refresh this page.')
    try:
        shop = _create_shop(pending)
    except Exception:
        PendingSignup.objects.filter(pk=pending.pk).update(used=False)
        return _notice(request, 'We could not create your shop',
                       'Nothing was charged. Please try the link again in a minute, or contact us.',
                       request.path, 'Try again', 500)
    mails.send_welcome(shop, pending.email)                   # best effort, never blocks the shop
    return _notice(request, 'Your shop is ready',
                   f'Sign in with {pending.email} and the password you chose. Your {settings.TRIAL_DAYS}-day free trial has started.',
                   shop_url(request, pending.subdomain, '/login/'), 'Go to my shop')
