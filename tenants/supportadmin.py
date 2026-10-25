"""Support tickets, the platform owner's side (/superadmin/support/): every shop's tickets in one place."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import support
from .models import Ticket, TicketMessage
from .platform import platform_admin_required

STATUS_LABELS = dict(Ticket.STATUSES)


@platform_admin_required
def ticket_list(request):
    want = request.GET.get('status') or ''
    q = (request.GET.get('q') or '').strip()
    base = Ticket.objects.select_related('shop')
    counts = {k: base.filter(status=k).count() for k in STATUS_LABELS}
    rows = base
    if want in STATUS_LABELS:
        rows = rows.filter(status=want)
    if q:
        rows = rows.filter(Q(subject__icontains=q) | Q(shop__name__icontains=q) | Q(messages__body__icontains=q)).distinct()
    return render(request, 'app/platform_support.html', dict(
        rows=rows[:200], counts=counts, total=sum(counts.values()), want=want, q=q, states=STATUS_LABELS))


@platform_admin_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket.objects.select_related('shop'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'close':
            ticket.status = 'closed'
            ticket.save()
            messages.success(request, f'Ticket #{ticket.pk} was closed.')
        elif action == 'reopen':
            ticket.status = 'open'
            ticket.save()
            messages.success(request, f'Ticket #{ticket.pk} was reopened.')
        elif action == 'reply':
            body, err = support.clean_message(request.POST.get('body'))
            if err:
                messages.error(request, err)
            else:
                msg = TicketMessage.objects.create(ticket=ticket, from_shop=False, author=request.user.get_username()[:150], body=body)
                ticket.status = 'answered'
                ticket.shop_unread = True
                ticket.save()
                support.mail_shop(request, ticket, msg)
                messages.success(request, 'Your reply was sent to the shop.')
        else:
            messages.error(request, 'Unknown action.')
        return redirect('platform_ticket', pk=ticket.pk)
    return render(request, 'app/platform_ticket.html', dict(ticket=ticket, thread=ticket.messages.all()))
