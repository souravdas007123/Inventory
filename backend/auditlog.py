"""Audit log: kisne kya banaya / badla / hataya. Log fail ho to asli kaam kabhi nahi rukta."""
from .models import AuditLog


def record(who, action, target='', detail=''):
    """who = request ya user. Hamesha transaction ke BAHAR call karein."""
    try:
        user = getattr(who, 'user', who)
        name = user.get_username() if hasattr(user, 'get_username') else str(user or '')
        AuditLog.objects.create(user=name[:150], action=str(action)[:20], target=str(target)[:60],
                                detail=str(detail)[:500])
    except Exception:   # noqa: BLE001 - logging se kabhi bill/stock ka kaam na ruke
        pass
