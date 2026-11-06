"""Restoring a shop from one of its own backups.

The shop only ASKS (Backup page -> "Ask us to restore"); nothing about its data changes. The platform owner sees the request in
/superadmin/restores/, checks it with the shop if needed, and starts it. The work itself is in restore.py."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from backend import access, auditlog, backup, tenancy

from . import restore, support
from .models import RestoreRequest
from .platform import platform_admin_required, shop_name_to_type

log = logging.getLogger(__name__)
NOTE_MAX = 300


def _shop(request):
    shop = getattr(request, 'tenant', None)
    if not tenancy.on_shop_site() or shop is None or shop.schema_name == settings.PUBLIC_SCHEMA_NAME:
        raise Http404
    return shop


def shop_context(request):
    """What the shop's Backup page needs to show the request form and its recent requests."""
    shop = _shop(request)
    return dict(restores=list(RestoreRequest.objects.filter(shop=shop)[:5]),
                restore_open=RestoreRequest.objects.filter(shop=shop, status__in=RestoreRequest.OPEN).exists(),
                restore_backups=backup.list_backups(), restoring_now=shop.restoring)


@login_required
@access.owner_required
@require_POST
def request_restore(request):
    shop = _shop(request)
    name = (request.POST.get('backup') or '').strip()
    note = ' '.join((request.POST.get('note') or '').split())[:NOTE_MAX]
    if name not in {b['name'] for b in backup.list_backups()}:
        messages.error(request, 'Please choose one of the backups in the list.')
    elif not request.POST.get('understand'):
        messages.error(request, 'Please tick the box to confirm that you understand what a restore does.')
    elif RestoreRequest.objects.filter(shop=shop, status__in=RestoreRequest.OPEN).exists():
        messages.error(request, 'You already have a restore request that is waiting or running.')
    else:
        req = RestoreRequest.objects.create(shop=shop, backup_name=name, note=note, requested_by=request.user.get_username()[:150])
        auditlog.record(request, 'BACKUP', 'Restore request', name)
        _mail_platform(request, req)
        messages.success(request, 'Your restore request was sent. We will check it with you and restore the backup. '
                                  'You can see its progress on this page.')
    return redirect('backup')


@login_required
@access.owner_required
@require_POST
def cancel_request(request, pk):
    shop = _shop(request)
    req = get_object_or_404(RestoreRequest, pk=pk, shop=shop)
    if RestoreRequest.objects.filter(pk=req.pk, status='requested').update(status='cancelled', finished_at=timezone.now(),
                                                                           message='Cancelled by the shop.'):
        messages.success(request, 'The restore request was cancelled.')
    else:
        messages.error(request, 'This request cannot be cancelled now.')
    return redirect('backup')


def _mail_platform(request, req):
    try:
        from . import site
        to = list(getattr(settings, 'ALERT_EMAILS', []) or []) or [site.current().get('contact_email') or '']
        support._send(f'[Symaso] Restore request from {req.shop.name}',
                      f'Shop: {req.shop.name}\nBackup: {req.backup_name}\nAsked by: {req.requested_by}\n'
                      f'Reason: {req.note or "-"}\n\nOpen it: {support.platform_url(request, "/superadmin/restores/")}\n', to)
    except Exception:
        log.exception('Could not e-mail the restore request')


# ---------------------------------------------------------------- the platform owner's side

def waiting_count():
    return RestoreRequest.objects.filter(status='requested').count()


@platform_admin_required
def restore_list(request):
    rows = list(RestoreRequest.objects.select_related('shop')[:100])
    now = timezone.now()
    for r in rows:
        r.can_undo = restore.can_undo(r, now)
        r.stuck = restore.is_stuck(r, now) or (r.status == 'failed' and r.shop.restoring)
        r.word = shop_name_to_type(r.shop)
        r.kept_days = restore.KEEP_DAYS
    return render(request, 'app/platform_restores.html', dict(rows=rows, waiting=sum(1 for r in rows if r.status == 'requested'),
                                                              keep_days=restore.KEEP_DAYS))


@platform_admin_required
@require_POST
def restore_action(request, pk):
    req = get_object_or_404(RestoreRequest.objects.select_related('shop'), pk=pk)
    action = request.POST.get('action')
    typed = (request.POST.get('confirm') or '').strip().lower()
    needs_word = action in ('start', 'undo')
    if needs_word and typed != shop_name_to_type(req.shop):
        messages.error(request, 'The shop name you typed does not match, so nothing was done.')
        return redirect('platform_restores')
    try:
        if action == 'start':
            restore.start(req.pk)
            messages.success(request, f'The restore of {req.shop.name} has started. The shop shows a "please wait" page until it is '
                                      'finished. This page shows the result; reload it in a few minutes.')
        elif action == 'undo':
            restore.undo(req.pk)
            messages.success(request, f'Done. {req.shop.name} has the data it had before the restore.')
        elif action == 'recover':
            restore.recover(req.pk)
            messages.success(request, f'{req.shop.name} was put back as it was before the restore.')
        elif action == 'reject':
            reason = ' '.join((request.POST.get('reason') or '').split())[:200] or 'Declined.'
            if RestoreRequest.objects.filter(pk=req.pk, status='requested').update(status='rejected', message=reason,
                                                                                   finished_at=timezone.now()):
                messages.success(request, 'The request was declined. The shop can see your reason.')
            else:
                messages.error(request, 'This request is not waiting any more.')
        else:
            messages.error(request, 'Unknown action.')
    except restore.RestoreError as e:
        messages.error(request, str(e))
    return redirect('platform_restores')
