"""The platform owner's panel (/superadmin/): every shop with its status, plan, usage and payments, and the buttons to
mark a payment, extend a trial, or switch a shop off and on. Only on the main site, only for platform admins."""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Count, Max, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from backend import tenancy

from . import site
from .models import Payment, Shop

log = logging.getLogger(__name__)
STATE_LABELS = {'trial': 'Free trial', 'active': 'Paid', 'locked': 'Locked', 'paused': 'Paused'}


def is_platform_admin(user):
    if not (user and user.is_authenticated and user.is_active and user.is_superuser):
        return False
    names = [n.lower() for n in getattr(settings, 'PLATFORM_ADMINS', [])]
    return not names or user.get_username().lower() in names


def platform_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not tenancy.is_platform():
            raise Http404                                  # a shop's own address has no such page
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_platform_admin(request.user):
            raise Http404                                  # others are not told that the page exists
        return view(request, *args, **kwargs)
    return wrapped


def prices():
    return site.prices()


def _schema_size_mb(schema):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'", [schema])
            return round(cur.fetchone()[0] / 1048576, 1)
    except Exception:
        log.exception('Could not measure the schema %s', schema)
        return None


def shop_stats(shop, fresh=False):
    """Usage of one shop, read from inside its own schema. Cached for 5 minutes in the list (fresh=True skips the cache)."""
    key = f'shop-stats:{shop.schema_name}'
    if not fresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    from django_tenants.utils import schema_context

    from backend.models import Bill, Product
    out = dict(bills=0, products=0, users=0, last_login=None, last_bill=None, size_mb=None)
    try:
        with schema_context(shop.schema_name):
            out['bills'] = Bill.objects.filter(status='FINAL').count()
            out['products'] = Product.objects.count()
            out['last_bill'] = Bill.objects.aggregate(d=Max('date'))['d']
            agg = get_user_model().objects.aggregate(n=Count('pk'), last=Max('last_login'))
            out['users'], out['last_login'] = agg['n'], agg['last']
    except Exception:
        log.exception('Could not read the shop %s', shop.schema_name)
    out['size_mb'] = _schema_size_mb(shop.schema_name)
    cache.set(key, out, 300)
    return out


def _row(shop, today, fresh=False):
    domain = shop.domains.filter(is_primary=True).first() or shop.domains.first()
    state = tenancy.shop_state(shop, today)
    return dict(shop=shop, domain=domain.domain if domain else '', state=state, state_label=STATE_LABELS[state],
                days_left=tenancy.days_left(shop, today), stats=shop_stats(shop, fresh))


def _shop_link(request, domain, path='/'):
    if not domain:
        return ''
    host = request.get_host()
    port = host.split(':', 1)[1] if ':' in host else ''
    return f"{request.scheme}://{domain}{':' + port if port else ''}{path}"


@platform_admin_required
def dashboard(request):
    today = timezone.localdate()
    q = (request.GET.get('q') or '').strip()
    want = request.GET.get('state') or ''
    shops = Shop.objects.exclude(schema_name=settings.PUBLIC_SCHEMA_NAME)
    all_rows = [_row(s, today) for s in shops[:500]]
    counts = {k: sum(1 for r in all_rows if r['state'] == k) for k in STATE_LABELS}
    monthly = sum(1 for r in all_rows if r['state'] == 'active' and r['shop'].plan == 'monthly')
    yearly = sum(1 for r in all_rows if r['state'] == 'active' and r['shop'].plan == 'yearly')
    rows = all_rows
    if want in STATE_LABELS:
        rows = [r for r in rows if r['state'] == want]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in ' '.join([r['shop'].name, r['shop'].owner_name, r['shop'].email,
                                                       r['shop'].phone, r['domain']]).lower()]
    for r in rows:
        r['url'] = _shop_link(request, r['domain'], '/login/')
    total = Payment.objects.aggregate(s=Sum('amount'))['s'] or Decimal('0')
    month_total = Payment.objects.filter(paid_on__year=today.year, paid_on__month=today.month).aggregate(s=Sum('amount'))['s'] or Decimal('0')
    return render(request, 'app/platform.html', dict(
        rows=rows, counts=counts, total_shops=len(all_rows), q=q, want=want, states=STATE_LABELS,
        new_week=sum(1 for r in all_rows if r['shop'].created_on.date() >= today - timedelta(days=7)),
        mrr=round(monthly * prices()['monthly'] + yearly * prices()['yearly'] / 12),
        revenue_total=total, revenue_month=month_total, prices=prices()))


@platform_admin_required
def shop_detail(request, pk):
    shop = get_object_or_404(Shop.objects.exclude(schema_name=settings.PUBLIC_SCHEMA_NAME), pk=pk)
    today = timezone.localdate()
    row = _row(shop, today, fresh=True)
    row['url'] = _shop_link(request, row['domain'], '/login/')
    return render(request, 'app/platform_shop.html', dict(
        row=row, shop=shop, payments=shop.payments.all()[:50], prices=prices(), today=today,
        paid_total=shop.payments.aggregate(s=Sum('amount'))['s'] or Decimal('0')))


def _log(shop, text):
    shop.notes = f'{timezone.localdate():%Y-%m-%d}: {text}\n{shop.notes}'


def _money(raw, default):
    raw = (raw or '').strip()
    if not raw:
        return Decimal(default)
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError('The amount is not a number.')
    if not Decimal('1') <= value <= Decimal('1000000'):
        raise ValueError('The amount must be between 1 and 10,00,000.')
    return value.quantize(Decimal('0.01'))


def _apply(request, shop, today):
    """Does the button that was pressed. Raises ValueError with a message for a wrong entry."""
    action = request.POST.get('action')
    if action == 'paid':
        plan = request.POST.get('plan')
        if plan not in ('monthly', 'yearly'):
            raise ValueError('Choose monthly or yearly.')
        amount = _money(request.POST.get('amount'), prices()[plan])
        start = max(today, shop.paid_until or today)       # a payment made before the end of a period extends it
        end = tenancy.add_months(start, 1 if plan == 'monthly' else 12)
        Payment.objects.create(shop=shop, plan=plan, amount=amount, paid_on=today, period_from=start, period_to=end,
                               note=(request.POST.get('note') or '').strip()[:200])
        shop.plan, shop.paid_until = plan, end
        _log(shop, f'payment Rs {amount} for {plan} plan, paid until {end:%d %b %Y}')
        return f'Payment of Rs {amount} saved. The shop is paid until {end:%d %b %Y}.'
    if action == 'trial':
        if shop.plan != 'trial':
            raise ValueError('Only a shop on the free trial can get more trial days.')
        try:
            days = int(request.POST.get('days') or 0)
        except ValueError:
            raise ValueError('Enter the number of days.')
        if not 1 <= days <= 365:
            raise ValueError('Days must be between 1 and 365.')
        shop.trial_ends = max(today, shop.trial_ends or today) + timedelta(days=days)
        _log(shop, f'trial extended by {days} days, now until {shop.trial_ends:%d %b %Y}')
        return f'The trial now ends on {shop.trial_ends:%d %b %Y}.'
    if action in ('pause', 'resume'):
        shop.is_paused = action == 'pause'
        _log(shop, 'shop switched off' if shop.is_paused else 'shop switched on')
        return 'The shop is switched off.' if shop.is_paused else 'The shop is switched on.'
    if action == 'note':
        text = (request.POST.get('note') or '').strip()[:500]
        if not text:
            raise ValueError('Write the note first.')
        _log(shop, text)
        return 'Note saved.'
    raise ValueError('Unknown action.')


@platform_admin_required
@require_POST
def shop_action(request, pk):
    shop = get_object_or_404(Shop.objects.exclude(schema_name=settings.PUBLIC_SCHEMA_NAME), pk=pk)
    try:
        with transaction.atomic():
            done = _apply(request, shop, timezone.localdate())
            shop.save()
        cache.delete(f'shop-stats:{shop.schema_name}')
        messages.success(request, done)
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('platform_shop', pk=shop.pk)


@platform_admin_required
def site_settings(request):
    """The website settings page: WhatsApp number, contact e-mail, a notice bar and the plan prices."""
    values = site.current()
    if request.method == 'POST':
        values, errors = tenancy.clean_site_form(request.POST)
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            site.save(values)
            messages.success(request, 'Website settings saved. The home page shows them within a minute.')
            return redirect('platform_site')
    return render(request, 'app/platform_site.html', dict(values=values, standard=settings.PLAN_PRICES))
