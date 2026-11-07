"""Sign-in history in the Audit Log: LOGIN, LOGOUT and LOGIN FAILED (with the IP address),
and a per-user summary (last login, last action) for the Users page. Logging never blocks a sign-in."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models import Count, Max
from django.dispatch import receiver
from django.utils import timezone

from . import auditlog
from .models import AuditLog
from .security import client_ip

LOGIN, LOGOUT, FAILED = 'LOGIN', 'LOGOUT', 'LOGIN FAILED'


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    auditlog.record(user, LOGIN, 'Session', f"IP {client_ip(request)}" if request else '')


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        auditlog.record(user, LOGOUT, 'Session', f"IP {client_ip(request)}" if request else '')


@receiver(user_login_failed)
def _failed(sender, credentials, request=None, **kwargs):
    name = str((credentials or {}).get('username', '') or '')[:150]
    auditlog.record(name or '(blank)', FAILED, 'Session', f"IP {client_ip(request)}" if request else '')


def user_rows():
    """One row per user: last login, last action (other than signing in/out) and what they did in the last 7 days."""
    User = get_user_model()
    users = list(User.objects.order_by('username')[:200])
    names = [u.get_username() for u in users]
    sessions = (LOGIN, LOGOUT, FAILED)
    last = dict(AuditLog.objects.filter(user__in=names).exclude(action__in=sessions)
                .values_list('user').annotate(m=Max('id')))
    ids = list(last.values())
    detail = {a.pk: a for a in AuditLog.objects.filter(pk__in=ids)}
    week = dict(AuditLog.objects.filter(user__in=names, created_at__gte=timezone.now() - timedelta(days=7))
                .exclude(action__in=sessions).values_list('user').annotate(c=Count('id')))
    failed = dict(AuditLog.objects.filter(user__in=names, action=FAILED,
                                          created_at__gte=timezone.now() - timedelta(days=7))
                  .values_list('user').annotate(c=Count('id')))
    from . import access, staff_access
    codes = staff_access.codenames_for_users([u for u in users if not u.is_superuser])
    rows = []
    for u in users:
        name = u.get_username()
        owner = access.is_owner(u)
        a = detail.get(last.get(name))
        rows.append(dict(
            pk=u.pk, username=name, full_name=u.get_full_name(), active=u.is_active, owner=owner,
            role='Owner' if owner else staff_access.role_of(staff_access.grid_from(codes.get(u.pk, set()))),
            last_login=u.last_login, last_action=(f"{a.action} {a.target}".strip() + (f" · {a.detail}" if a.detail else '')) if a else '',
            last_action_at=a.created_at if a else None, week=week.get(name, 0), failed=failed.get(name, 0)))
    return rows
