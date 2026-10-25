"""One sign-in page for everybody: the main site's /login/.

The person types his email (or username) and password. If they belong to the main shop he is signed in there. Otherwise we
find his shop by the email of its owner (or by the shop address typed in the optional box, for staff), check the password
inside that shop, and send him to his shop's own address with a one-time pass that signs him in there (the main site and
every shop are different web addresses, so a sign-in cannot simply be shared)."""
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django_tenants.utils import schema_context

from backend import tenancy
from backend.security import LOCK_SECONDS, MAX_ATTEMPTS, client_ip

from .models import Domain, LoginToken, Shop

BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _safe_next(value):
    value = (value or '').strip()
    return value if value.startswith('/') and not value.startswith('//') and len(value) <= 300 else ''


def _shop_from_address(text):
    """The shop for what a person typed in the 'shop address' box: 'sharma', 'sharma.medsync.example.com' or a full link."""
    text = (text or '').strip().lower()
    for prefix in ('https://', 'http://'):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split('/')[0].split(':')[0]
    base = (getattr(settings, 'SHOP_BASE_DOMAIN', '') or '').lower()
    if base and text.endswith('.' + base):
        text = text[:-len(base) - 1]
    name, error = tenancy.clean_subdomain(text)
    if error:
        return None
    return Shop.objects.filter(schema_name=tenancy.schema_name_for(name)).first()


def _check_in_shop(request, shop, username, password):
    with schema_context(shop.schema_name):
        user = authenticate(request, username=username, password=password)
        return user.pk if user else None


def _find(request, ident, password, shop_text):
    """('main', user) or ('shop', (shop, user_id)) or None."""
    if shop_text:
        shop = _shop_from_address(shop_text)
        if shop:
            uid = _check_in_shop(request, shop, ident, password)
            if uid:
                return 'shop', (shop, uid)
        return None
    user = authenticate(request, username=ident, password=password)
    if user:
        return 'main', user
    if '@' in ident:
        email = ident.lower()
        for shop in Shop.objects.filter(email__iexact=email):
            uid = _check_in_shop(request, shop, email, password)
            if uid:
                return 'shop', (shop, uid)
    return None


def _shop_url(request, shop, token):
    domain = Domain.objects.filter(tenant=shop).order_by('-is_primary', 'id').first()
    if domain is None:
        return None
    return f'{request.scheme}://{domain.domain}{tenancy.port_suffix(request)}/sso/{token}/'


def main_login(request):
    """The sign-in page of the main site (only used while many shops is on)."""
    next_url = _safe_next(request.POST.get('next') or request.GET.get('next'))
    ctx = dict(main_site=True, next=next_url, ident='', shop_text='')
    if request.method != 'POST':
        return render(request, 'app/login.html', ctx)
    ident = (request.POST.get('username') or '').strip()
    password = request.POST.get('password') or ''
    shop_text = (request.POST.get('shop') or '').strip()
    ctx.update(ident=ident, shop_text=shop_text)
    key = 'login-fail:' + hashlib.sha256(f'{client_ip(request)}|{ident.lower()}'.encode()).hexdigest()
    if cache.get(key, 0) >= MAX_ATTEMPTS:
        return render(request, 'app/login.html', dict(ctx, locked=True), status=429)
    found = _find(request, ident, password, shop_text) if ident and password else None
    if not found:
        cache.set(key, cache.get(key, 0) + 1, LOCK_SECONDS)
        return render(request, 'app/login.html', dict(ctx, failed=True))
    cache.delete(key)
    kind, value = found
    if kind == 'main':
        auth_login(request, value, backend=BACKEND)
        dest = next_url or settings.LOGIN_REDIRECT_URL
        return redirect(dest)
    shop, uid = value
    LoginToken.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()
    token = tenancy.new_token()
    url = _shop_url(request, shop, token)
    if url is None:
        return render(request, 'app/login.html', dict(ctx, failed=True))
    LoginToken.objects.create(token=token, schema_name=shop.schema_name, user_id=uid, next_url=next_url)
    return redirect(url)


def sso(request, token):
    """Opened on a shop's own address: turns a valid one-time pass into a signed-in session."""
    shop = getattr(request, 'tenant', None)
    if not tenancy.on_shop_site() or shop is None:
        from django.http import Http404
        raise Http404
    fresh = timezone.now() - timedelta(seconds=LoginToken.VALID_SECONDS)
    t = LoginToken.objects.filter(token=token, used=False, schema_name=shop.schema_name, created_at__gte=fresh).first()
    if t is None or LoginToken.objects.filter(pk=t.pk, used=False).update(used=True) != 1:
        return render(request, 'app/notice.html', dict(
            title='This sign-in link is not valid any more',
            message='It was already used, or it was opened too late. Please sign in again from the main page.',
            link_url=_main_login_url(request), link_text='Sign in'), status=400)
    user = get_user_model().objects.filter(pk=t.user_id, is_active=True).first()
    if user is None:
        return redirect('/login/')
    auth_login(request, user, backend=BACKEND)
    dest = t.next_url if url_has_allowed_host_and_scheme(t.next_url, {request.get_host()}) and _safe_next(t.next_url) else '/'
    return redirect(dest)


def _main_login_url(request):
    return f"{request.scheme}://{settings.SHOP_BASE_DOMAIN}{tenancy.port_suffix(request)}/login/"
