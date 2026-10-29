"""Sign in and sign up with Google (only on the main site, only while many shops is on and the Google keys are set).

How it works, in short:
* The person clicks "Continue with Google" -> /auth/google/?mode=login|signup. We send him to Google with a random `state` and `nonce`
  that we keep in his session.
* Google sends him back to /auth/google/callback/ with a `code`. We swap the code for an id token by talking to Google directly
  over https (so the token cannot be forged by the visitor) and check who it is for, that it is fresh, that the nonce is ours and
  that Google says the e-mail address is verified.
* Sign in: the e-mail must be the e-mail of a shop's owner. He is then passed to his own shop with the same one-time pass the
  password sign-in uses (tenants/mainlogin.py), so two-step verification still applies.
* Sign up: the verified e-mail is kept in the session and the usual sign-up form opens without the e-mail and password boxes. The
  shop is made at once (Google has already checked the e-mail), and he is signed in to it.
Nothing here ever trusts an e-mail that Google did not mark as verified, and a Google sign-in never creates or changes a password."""
import base64
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from backend import tenancy
from backend.security import client_ip

log = logging.getLogger(__name__)

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
SIGNUP_SESSION_KEY = 'google_signup'
SIGNUP_VALID_SECONDS = 30 * 60
MAX_TRIES = 20                      # Google round trips per address in 10 minutes


class GoogleError(Exception):
    """A Google sign-in that cannot go on. The message is safe to show to the person."""


def configured():
    return bool(getattr(settings, 'MULTI_SHOP', False) and getattr(settings, 'GOOGLE_CLIENT_ID', '')
                and getattr(settings, 'GOOGLE_CLIENT_SECRET', ''))


def signup_data(request):
    """{'email', 'name'} kept after a successful Google check for the sign-up form, or None (also when it is too old)."""
    data = request.session.get(SIGNUP_SESSION_KEY)
    if not isinstance(data, dict) or not data.get('email'):
        return None
    if time.time() - float(data.get('at') or 0) > SIGNUP_VALID_SECONDS:
        request.session.pop(SIGNUP_SESSION_KEY, None)
        return None
    return data


def _notice(request, title, message, link_url='', link_text='', status=200):
    return render(request, 'app/notice.html', dict(title=title, message=message, link_url=link_url, link_text=link_text), status=status)


def redirect_uri(request):
    return f'{request.scheme}://{request.get_host()}/auth/google/callback/'


def exchange_code(code, uri):
    """Swaps the code for Google's answer (a dict with 'id_token'). The only place that talks to the network."""
    body = urllib.parse.urlencode(dict(
        code=code, client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET,
        redirect_uri=uri, grant_type='authorization_code')).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        log.exception('Google did not answer the token request')
        raise GoogleError('We could not reach Google. Please try again in a minute.')


def id_token_claims(id_token):
    """The content of the id token. No signature check is needed because the token came straight from Google over https."""
    try:
        part = id_token.split('.')[1]
        part += '=' * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part.encode()).decode('utf-8'))
    except Exception:
        raise GoogleError('Google sent an answer we could not read. Please try again.')
    if not isinstance(claims, dict):
        raise GoogleError('Google sent an answer we could not read. Please try again.')
    return claims


def check_claims(claims, nonce, now=None):
    """(email, name) when the token is for this site and this visit, else GoogleError."""
    now = time.time() if now is None else now
    aud = claims.get('aud')
    if claims.get('iss') not in ISSUERS or aud != settings.GOOGLE_CLIENT_ID:
        raise GoogleError('This Google sign-in was not meant for MedSync. Please try again.')
    try:
        if float(claims.get('exp') or 0) < now:
            raise GoogleError('This Google sign-in has expired. Please try again.')
    except (TypeError, ValueError):
        raise GoogleError('Google sent an answer we could not read. Please try again.')
    if not nonce or not hmac.compare_digest(str(claims.get('nonce') or ''), str(nonce)):
        raise GoogleError('This Google sign-in does not match your visit. Please start again.')
    email = str(claims.get('email') or '').strip().lower()
    if not email or claims.get('email_verified') not in (True, 'true'):
        raise GoogleError('Google has not verified this e-mail address, so we cannot use it.')
    return email, ' '.join(str(claims.get('name') or '').split())[:120]


@require_GET
def start(request):
    if not tenancy.is_platform() or not configured():
        raise Http404
    mode = 'signup' if request.GET.get('mode') == 'signup' else 'login'
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    request.session['google_oauth'] = dict(state=state, nonce=nonce, mode=mode, at=time.time())
    query = urllib.parse.urlencode(dict(
        client_id=settings.GOOGLE_CLIENT_ID, redirect_uri=redirect_uri(request), response_type='code',
        scope='openid email profile', state=state, nonce=nonce, prompt='select_account'))
    return redirect(f'{AUTH_URL}?{query}')


def _fail(request, error, mode):
    back = '/signup/' if mode == 'signup' else '/login/'
    return _notice(request, 'Google sign-in did not work', str(error), back, 'Back', 400)


@require_GET
def callback(request):
    if not tenancy.is_platform() or not configured():
        raise Http404
    saved = request.session.pop('google_oauth', None)
    mode = saved.get('mode') if isinstance(saved, dict) else 'login'
    rate = 'google-ip:' + client_ip(request)
    if cache.get(rate, 0) >= MAX_TRIES:
        return _notice(request, 'Too many attempts', 'Please try again after a few minutes.', '/login/', 'Back', 429)
    cache.set(rate, cache.get(rate, 0) + 1, 600)
    try:
        if not isinstance(saved, dict) or time.time() - float(saved.get('at') or 0) > 600:
            raise GoogleError('This Google sign-in took too long or was already used. Please start again.')
        if request.GET.get('error'):
            raise GoogleError('Google sign-in was cancelled.')
        if not hmac.compare_digest(str(request.GET.get('state') or ''), str(saved.get('state') or '')) or not request.GET.get('code'):
            raise GoogleError('This Google sign-in does not match your visit. Please start again.')
        answer = exchange_code(request.GET['code'], redirect_uri(request))
        email, name = check_claims(id_token_claims(str(answer.get('id_token') or '')), saved.get('nonce'))
    except GoogleError as e:
        return _fail(request, e, mode)
    return _sign_up(request, email, name) if mode == 'signup' else _sign_in(request, email)


def owner_of(shop, email):
    """The id of the shop owner with this e-mail inside the shop, or None."""
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    with schema_context(shop.schema_name):
        user = get_user_model().objects.filter(is_active=True, email__iexact=email).order_by('-is_superuser', 'id').first()
        return user.pk if user else None


def _sign_in(request, email):
    from .mainlogin import pass_to_shop
    from .models import Shop
    shop = Shop.objects.filter(email__iexact=email).first()
    uid = owner_of(shop, email) if shop else None
    if not uid:
        return _notice(request, 'No shop found for this Google account',
                       f'No MedSync shop uses {email}. Sign up first, or sign in with the e-mail and password of your shop. '
                       'Staff members sign in with their username and password.', '/signup/', 'Create your shop', 404)
    response = pass_to_shop(request, shop, uid, '')
    if response is None:
        return _notice(request, 'We could not open your shop', 'Please try again, or sign in with your password.', '/login/', 'Sign in', 500)
    return response


def _sign_up(request, email, name):
    from .models import Shop
    if Shop.objects.filter(email__iexact=email).exists():
        return _notice(request, 'You already have a shop', f'A shop already uses {email}. Please sign in with Google or with your password.',
                       '/login/', 'Sign in')
    request.session[SIGNUP_SESSION_KEY] = dict(email=email, name=name, at=time.time())
    return redirect('/signup/')
