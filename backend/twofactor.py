"""2-step sign-in (opt-in): after the right password, a 6-digit code is e-mailed; the person types it to get in.

Safety rules, so that nobody is locked out by accident:
* It is switched on only by the person himself, and only after a code sent to his e-mail was typed correctly (so the e-mail works).
* 8 recovery codes are shown once; each works one time instead of the e-mailed code.
* "Remember this browser for 30 days" skips the code on that browser (a changed password ends it).
* The platform owner can switch it off for a shop's owner, a shop owner can remove it for staff in the admin panel,
  and `manage.py two_step_off <username>` works on the server.
Codes are never saved in plain text; a wrong code can be tried 5 times, then the person must sign in again."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from . import auditlog

log = logging.getLogger(__name__)
CODE_MINUTES = 10
MAX_TRIES = 5
MAX_SENDS = 5                 # e-mails for one sign-in attempt
RESEND_SECONDS = 30
SETUP_SENDS_PER_HOUR = 5
DEVICE_DAYS = 30
DEVICE_COOKIE = 'ms_td'
DEVICE_SALT = 'medsync-2step-device'
SESSION_KEY = 'pre2fa'
SETUP_KEY = 'setup2fa'
RECOVERY_COUNT = 8


# ---------- small helpers ----------

def is_on(user):
    """True when this user has 2-step sign-in. Never raises: if the table cannot be read the person is not locked out."""
    try:
        from .models import TwoFactor
        return TwoFactor.objects.filter(user_id=user.pk).exists()
    except Exception:
        log.exception('Could not read the 2-step sign-in setting')
        return False


def _hash(user_id, value):
    return hmac.new(settings.SECRET_KEY.encode(), f'{user_id}:{value}'.encode(), hashlib.sha256).hexdigest()


def new_code():
    return f'{secrets.randbelow(10 ** 6):06d}'


def new_recovery_codes():
    """[(shown, stored hash)]: codes like 'K7F2-9QXM' (no look-alike letters), shown once."""
    alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
    out = []
    for _ in range(RECOVERY_COUNT):
        raw = ''.join(secrets.choice(alphabet) for _ in range(8))
        out.append((f'{raw[:4]}-{raw[4:]}', raw))
    return out


def clean_recovery(text):
    return ''.join(ch for ch in (text or '').upper() if ch.isalnum())


def _password_stamp(user):
    return hashlib.sha256((user.password or '').encode()).hexdigest()[:16]


def device_trusted(request, user):
    raw = request.COOKIES.get(DEVICE_COOKIE)
    if not raw:
        return False
    try:
        data = signing.loads(raw, salt=DEVICE_SALT, max_age=DEVICE_DAYS * 86400)
    except signing.BadSignature:
        return False
    return data.get('u') == user.pk and hmac.compare_digest(str(data.get('p', '')), _password_stamp(user))


def remember_device(response, request, user):
    value = signing.dumps({'u': user.pk, 'p': _password_stamp(user)}, salt=DEVICE_SALT)
    response.set_cookie(DEVICE_COOKIE, value, max_age=DEVICE_DAYS * 86400, httponly=True, secure=request.is_secure(), samesite='Lax')


def mail_code(user, code, purpose='sign in'):
    """Sends the code. Returns True when it was handed to the mail server."""
    if not user.email:
        return False
    try:
        send_mail(f'Your MedSync code: {code}',
                  f'Your MedSync {purpose} code is {code}.\n\nIt works for {CODE_MINUTES} minutes and only once. '
                  f'If you did not try to sign in, please change your password.\n\nMedSync', settings.DEFAULT_FROM_EMAIL,
                  [user.email], fail_silently=False)
        return True
    except Exception:
        log.exception('Could not send the 2-step code')
        return False


def _safe_next(request, url):
    if url and url != '/login/' and url_has_allowed_host_and_scheme(url, {request.get_host()}, require_https=request.is_secure()):
        return url
    return settings.LOGIN_REDIRECT_URL


# ---------- signing in ----------

def begin(request, user, backend, next_url=''):
    """Called where a person has just proven his password. Returns None when he can be signed in now (2-step is off, or this
    browser is remembered), otherwise starts the code step and returns the redirect to it."""
    if not is_on(user) or device_trusted(request, user):
        return None
    pending = dict(uid=user.pk, backend=backend, next=next_url or '', tries=0, sends=0, h='', exp=0, last=0)
    request.session[SESSION_KEY] = pending
    sent = _send_sign_in_code(request, user)
    if not sent:
        request.session['pre2fa_note'] = ('We could not e-mail a code' + (' (there is no e-mail address on this account)' if not user.email else '') +
                                          '. Use one of your recovery codes instead.')
    return redirect('login_verify')


def _send_sign_in_code(request, user):
    pending = request.session.get(SESSION_KEY) or {}
    code = new_code()
    pending.update(h=_hash(user.pk, code), exp=(timezone.now() + timedelta(minutes=CODE_MINUTES)).timestamp(),
                   sends=pending.get('sends', 0) + 1, last=timezone.now().timestamp())
    request.session[SESSION_KEY] = pending
    return mail_code(user, code)


def _fail(request, user, why):
    auditlog.record(user, 'LOGIN FAILED', 'Session', why)


def verify(request):
    pending = request.session.get(SESSION_KEY)
    user = get_user_model().objects.filter(pk=pending['uid'], is_active=True).first() if pending else None
    if not pending or user is None:
        request.session.pop(SESSION_KEY, None)
        return redirect('login')
    now = timezone.now().timestamp()
    info = request.session.pop('pre2fa_note', '')
    error = ''
    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            if pending['sends'] >= MAX_SENDS:
                error = 'You have asked for too many codes. Please sign in again, or use a recovery code.'
            elif now - pending['last'] < RESEND_SECONDS:
                error = f'Please wait {RESEND_SECONDS} seconds before asking for another code.'
            elif _send_sign_in_code(request, user):
                info = 'A new code was sent to your e-mail.'
            else:
                error = 'We could not e-mail a code. Use one of your recovery codes instead.'
        else:
            entered = ''.join(ch for ch in (request.POST.get('code') or '') if ch.isalnum()).upper()
            ok = False
            if len(entered) == 6 and entered.isdigit():
                ok = bool(pending['h']) and now < pending['exp'] and hmac.compare_digest(_hash(user.pk, entered), pending['h'])
            elif entered:
                ok = _use_recovery_code(user, entered)
            if ok:
                request.session.pop(SESSION_KEY, None)
                auth_login(request, user, backend=pending['backend'])
                resp = redirect(_safe_next(request, pending.get('next')))
                if request.POST.get('remember'):
                    remember_device(resp, request, user)
                return resp
            pending['tries'] += 1
            request.session[SESSION_KEY] = pending
            _fail(request, user, 'Wrong 2-step code')
            if pending['tries'] >= MAX_TRIES:
                request.session.pop(SESSION_KEY, None)
                request.session['pre2fa_note'] = 'Too many wrong codes. Please sign in again.'
                return redirect('login')
            error = 'That code is not right, or it has expired.'
    return render(request, 'app/twostep_verify.html', dict(
        error=error, info=info, email_hint=_mask(user.email), minutes=CODE_MINUTES,
        left=MAX_TRIES - pending['tries']), status=200)


def _mask(email):
    if not email or '@' not in email:
        return ''
    name, _, host = email.partition('@')
    return f"{name[:1]}{'*' * max(len(name) - 1, 1)}@{host}"


def _use_recovery_code(user, entered):
    """True (and the code is used up) when `entered` is one of the user's unused recovery codes."""
    from .models import TwoFactor
    tf = TwoFactor.objects.filter(user_id=user.pk).first()
    if tf is None:
        return False
    target = _hash(user.pk, entered)
    for stored in tf.recovery or []:
        if hmac.compare_digest(stored, target):
            tf.recovery = [s for s in tf.recovery if s != stored]
            tf.save(update_fields=['recovery'])
            auditlog.record(user, 'SECURITY', '2-step', 'A recovery code was used')
            return True
    return False


# ---------- turning it on and off (Security page) ----------

def start_setup(request, user):
    """E-mails the confirmation code. Returns an error text or ''."""
    if not user.email:
        return 'Your account has no e-mail address. Ask the owner to add one first.'
    key = f'2fa-setup:{user.pk}'
    n = cache.get(key, 0)
    if n >= SETUP_SENDS_PER_HOUR:
        return 'You asked for too many codes. Please try again in an hour.'
    cache.set(key, n + 1, 3600)
    code = new_code()
    request.session[SETUP_KEY] = dict(h=_hash(user.pk, code), exp=(timezone.now() + timedelta(minutes=CODE_MINUTES)).timestamp(), tries=0)
    if not mail_code(user, code, 'confirmation'):
        request.session.pop(SETUP_KEY, None)
        return 'We could not send the e-mail. Please try again later. Nothing was changed.'
    return ''


def finish_setup(request, user, entered):
    """Checks the confirmation code. On success turns 2-step on and returns the recovery codes to show, else None."""
    st = request.session.get(SETUP_KEY)
    entered = ''.join(ch for ch in (entered or '') if ch.isdigit())
    if not st:
        return None
    st['tries'] += 1
    request.session[SETUP_KEY] = st
    if st['tries'] > MAX_TRIES or timezone.now().timestamp() > st['exp']:
        request.session.pop(SETUP_KEY, None)
        return None
    if len(entered) != 6 or not hmac.compare_digest(_hash(user.pk, entered), st['h']):
        return None
    request.session.pop(SETUP_KEY, None)
    return turn_on(user, request)


def turn_on(user, request=None):
    from .models import TwoFactor
    pairs = new_recovery_codes()
    TwoFactor.objects.update_or_create(user=user, defaults=dict(recovery=[_hash(user.pk, raw) for _, raw in pairs]))
    auditlog.record(user, 'SECURITY', '2-step', 'Turned on')
    return [shown for shown, _ in pairs]


def turn_off(user):
    from .models import TwoFactor
    TwoFactor.objects.filter(user_id=user.pk).delete()
    auditlog.record(user, 'SECURITY', '2-step', 'Turned off')


def security_page(request):
    """Security page of every signed-in user: turn 2-step sign-in on or off, and make new recovery codes."""
    from django.contrib import messages
    user = request.user
    ctx = dict(active='security', new_codes=None)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'send':
            err = start_setup(request, user)
            messages.error(request, err) if err else messages.success(request, f'We sent a code to {_mask(user.email)}.')
        elif action == 'confirm':
            codes = finish_setup(request, user, request.POST.get('code'))
            if codes:
                messages.success(request, '2-step sign-in is now on. Please save your recovery codes.')
                ctx['new_codes'] = codes
            else:
                messages.error(request, 'That code is not right, or it has expired. Please ask for a new one.')
        elif action in ('disable', 'codes'):
            if not user.check_password(request.POST.get('password') or ''):
                messages.error(request, 'The password is not right, so nothing was changed.')
            elif action == 'disable':
                turn_off(user)
                messages.success(request, '2-step sign-in is now off.')
            elif is_on(user):
                ctx['new_codes'] = turn_on(user)
                messages.success(request, 'New recovery codes were made. The old ones do not work any more.')
        if ctx['new_codes'] is None:
            return redirect('security')
    on = is_on(user)
    left = 0
    if on:
        from .models import TwoFactor
        left = len((TwoFactor.objects.filter(user_id=user.pk).values_list('recovery', flat=True).first()) or [])
    ctx.update(on=on, left=left, confirming=bool(request.session.get(SETUP_KEY)), email_hint=_mask(user.email), has_email=bool(user.email),
               minutes=CODE_MINUTES)
    return render(request, 'app/security.html', ctx)
