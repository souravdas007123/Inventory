"""
Who may see and do what in the main app (not only in /admin/).

- Owner  = a superuser, or a staff user who may change Shop Settings. The owner can do everything.
- Everyone else can do only what the permissions given in the admin panel allow: add / change / delete / view
  of each model (for example only "Purchase: add + change" for a purchase clerk).
  As in the admin, "change" also lets the user see the list.
"""
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

APP = 'backend'


def is_owner(user):
    return bool(user and user.is_authenticated and user.is_active and (
        user.is_superuser or (user.is_staff and user.has_perm(f'{APP}.change_shopprofile'))))


def can(user, action, model):
    """action: 'view', 'add', 'change' or 'delete'; model: lower-case model name, e.g. 'purchase'."""
    if not (user and user.is_authenticated and user.is_active):
        return False
    if is_owner(user):
        return True
    actions = ('view', 'change') if action == 'view' else (action,)
    return any(user.has_perm(f'{APP}.{a}_{model}') for a in actions)


def perm_required(*rules):
    """View decorator. rules = ('add', 'purchase'), ('change', 'purchase'), ...: allowed when ANY rule is met."""
    def deco(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not any(can(request.user, act, model) for act, model in rules):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return deco


def owner_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_owner(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


# Menu entries that are not a plain list of one model (everything else follows its model's "view" permission)
NAV_RULES = {
    'dashboard': ('view', 'sale'), 'drug_register': ('view', 'bill'), 'supplier_prices': ('view', 'purchase'),
    'reorder': ('view', 'purchaseorder'), 'refill': ('view', 'bill'), 'expiry': ('add', 'purchasereturn'), 'salary': ('view', 'employee'),
    'import': 'owner', 'report': 'owner', 'gst': 'owner', 'ca_export': 'owner', 'health': 'owner', 'audit': 'owner', 'users': 'owner', 'gstr2b': 'owner', 'backup': 'owner', 'shop': 'owner', 'reset': 'owner',
    'stock_audits': 'owner',
}


def _count_tickets(request, user, platform=False):
    """New replies waiting for this shop, or (platform) tickets waiting for the platform owner. 0 when unreadable."""
    try:
        from tenants.models import Ticket
        if platform:
            from tenants.platform import is_platform_admin
            return Ticket.objects.filter(status='open').count() if is_platform_admin(user) else 0
        shop = getattr(request, 'tenant', None)
        return Ticket.objects.filter(shop=shop, shop_unread=True).count() if user.is_authenticated and shop is not None else 0
    except Exception:
        return 0


def template_context(request):
    """Adds `is_owner` and the menu (only what this user may open) to every template."""
    user = request.user
    from .views import nav_for       # imported here: views imports this module
    from . import tenancy
    ctx = {'is_owner': is_owner(user), 'nav': nav_for(user), 'shop_note': tenancy.shop_note(request),
           'platform_url': tenancy.platform_panel_url(user), 'platform_notice': ''}
    ctx['support_unread'] = ctx['open_tickets'] = 0
    ctx['maintenance_banner'] = ctx['maintenance_bar'] = ''
    if tenancy.on_shop_site():
        from tenants import maintenance, site
        ctx['platform_notice'] = site.shop_notice()
        ctx['maintenance_banner'] = maintenance.banner()
        ctx['support_unread'] = _count_tickets(request, user)
    elif getattr(settings, 'MULTI_SHOP', False) and tenancy.is_platform() and user.is_authenticated:
        ctx['open_tickets'] = _count_tickets(request, user, platform=True)
        from tenants import maintenance
        ctx['maintenance_bar'] = maintenance.platform_bar()
    return ctx
