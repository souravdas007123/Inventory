"""Helpers for sending alerts (low stock / expiry) and Purchase Orders by email / WhatsApp."""
import re
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone

from .models import Batch, Product, ShopProfile

MAX_LINES = 25


def collect_alerts(days=30):
    """List of low stock, out of stock, expiring soon and expired batches."""
    today = timezone.localdate()
    active = Product.objects.exclude(is_active=False).select_related('unit')
    live = Batch.objects.filter(qty__gt=0, expire_date__isnull=False).select_related('product', 'unit')
    return dict(
        today=today, days=days,
        out=list(active.filter(stock_qty=0).order_by('name')),
        low=list(active.filter(stock_qty__gt=0, stock_qty__lte=F('reorder_level')).order_by('stock_qty', 'name')),
        expiring=list(live.filter(expire_date__gte=today, expire_date__lte=today + timedelta(days=days))
                      .order_by('expire_date')),
        expired=list(live.filter(expire_date__lt=today).order_by('expire_date')),
    )


def _section(title, lines, total):
    if not total:
        return []
    out = [f"{title} ({total})"]
    out += [f"  - {line}" for line in lines[:MAX_LINES]]
    if total > MAX_LINES:
        out.append(f"  ...and {total - MAX_LINES} more")
    return out + ['']


def alert_digest_text(alerts, shop_name=''):
    """Plain-text digest (for both email and WhatsApp)."""
    head = f"{shop_name or 'MedSync'} - Stock alerts ({alerts['today']:%d %b %Y})"
    parts = [head, '']
    parts += _section('OUT OF STOCK', [p.name or str(p) for p in alerts['out']], len(alerts['out']))
    parts += _section('LOW STOCK', [f"{p.name} ({p.stock_display()})" for p in alerts['low']], len(alerts['low']))
    parts += _section('EXPIRED (still in stock)',
                      [f"{b.product.name if b.product else '-'} | {b.batch_number} | qty {b.qty} | {b.expire_date:%d %b %Y}"
                       for b in alerts['expired']], len(alerts['expired']))
    parts += _section(f"EXPIRING in {alerts['days']} days",
                      [f"{b.product.name if b.product else '-'} | {b.batch_number} | qty {b.qty} | {b.expire_date:%d %b %Y}"
                       for b in alerts['expiring']], len(alerts['expiring']))
    if len(parts) == 2:
        parts.append('All good - no alerts.')
    return '\n'.join(parts).strip()


def alert_recipients():
    emails = list(getattr(settings, 'ALERT_EMAILS', []) or [])
    if not emails:
        shop_email = ShopProfile.get().email
        if shop_email:
            emails = [shop_email]
    return emails


def send_alert_digest(recipients=None):
    """Sends the digest email. Returns (sent_count, recipients); (0, []) if there are no recipients."""
    recipients = recipients or alert_recipients()
    if not recipients:
        return 0, []
    shop = ShopProfile.get()
    alerts = collect_alerts()
    text = alert_digest_text(alerts, shop.name)
    count = len(alerts['out']) + len(alerts['low']) + len(alerts['expired']) + len(alerts['expiring'])
    sent = send_mail(f"[{shop.name}] Stock alerts - {count} item(s)", text,
                     settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
    return sent, recipients


def whatsapp_link(text, phone=''):
    """wa.me link (no API needed). A 10-digit number is assumed to be an Indian (+91) number."""
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) == 10:
        digits = '91' + digits
    base = f"https://wa.me/{digits}" if digits else "https://wa.me/"
    return f"{base}?text={quote(text[:1800])}"


def credit_reminder_link(shop_name, name, phone, balance):
    """WhatsApp link (wa.me, no API) to remind a customer about outstanding credit."""
    text = (f"Hello {name}, this is a reminder from {shop_name or 'MedSync'} that your outstanding balance of \u20b9{balance:,.2f} "
            f"is due. Please clear it at your earliest convenience. Thank you!")
    return whatsapp_link(text, phone)


def po_text(po, shop):
    """Plain text of a Purchase Order (email body / WhatsApp message)."""
    lines = [f"{shop.name} - Purchase Order {po.po_number}",
             f"Date: {po.date_created:%d %b %Y}",
             f"To: {po.supplier.company}", '', 'Items:']
    for n, item in enumerate(po.items.select_related('product', 'product__unit'), 1):
        unit = item.product.unit.name if item.product.unit_id else 'unit'
        lines.append(f"{n}. {item.product.name} - {item.order_qty} {unit}")
    lines += ['', 'Please supply as soon as possible. Thank you.']
    if shop.phone:
        lines.append(f"Contact: {shop.phone}")
    return '\n'.join(lines)
