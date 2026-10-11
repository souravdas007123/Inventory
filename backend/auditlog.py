"""Audit log: who created / changed / deleted what. If logging fails, the real work is never blocked."""
from .models import AuditLog


def record(who, action, target='', detail=''):
    """who = a request or a user. Always call this OUTSIDE a transaction."""
    try:
        user = getattr(who, 'user', who)
        name = user.get_username() if hasattr(user, 'get_username') else str(user or '')
        AuditLog.objects.create(user=name[:150], action=str(action)[:20], target=str(target)[:60],
                                detail=str(detail)[:500])
    except Exception:   # noqa: BLE001 - a logging failure must never block billing/stock work
        pass
