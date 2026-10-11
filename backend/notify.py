"""Alerts (low stock / expiry) aur Purchase Order ko email / WhatsApp se bhejne ke helpers."""
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
    """Low stock, out of stock, expiring soon aur expired batches ki list."""
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
        out.append(f"  ...aur {total - MAX_LINES} aur")
    return out + ['']


def alert_digest_text(alerts, shop_name=''):
    """Plain-text digest (email aur WhatsApp dono ke liye)."""
    head = f"{shop_name or 'MedSync'} - Stock alerts ({alerts['today']:%d %b %Y})"
    parts = [head, '']
    parts += _section('OUT OF STOCK', [p.name or str(p) for p in alerts['out']], len(alerts['out']))
    parts += _section('LOW STOCK', [f"{p.name} ({p.stock_display()})" for p in alerts['low']], len(alerts['low']))
    parts += _section('EXPIRED (stock me abhi bhi hai)',
                      [f"{b.product.name if b.product else '-'} | {b.batch_number} | qty {b.qty} | {b.expire_date:%d %b %Y}"
                       for b in alerts['expired']], len(alerts['expired']))
    parts += _section(f"EXPIRING in {alerts['days']} days",
                      [f"{b.product.name if b.product else '-'} | {b.batch_number} | qty {b.qty} | {b.expire_date:%d %b %Y}"
                       for b in alerts['expiring']], len(alerts['expiring']))
    if len(parts) == 2:
        parts.append('Sab theek hai - koi alert nahi.')
    return '\n'.join(parts).strip()


def alert_recipients():
    emails = list(getattr(settings, 'ALERT_EMAILS', []) or [])
    if not emails:
        shop_email = ShopProfile.get().email
        if shop_email:
            emails = [shop_email]
    return emails


def send_alert_digest(recipients=None):
    """Digest email bhejta hai. (sent_count, recipients) return karta hai; recipients na ho to (0, [])."""
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
    """wa.me link (koi API nahi chahiye). 10 digit number par India (+91) maan lete hain."""
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) == 10:
        digits = '91' + digits
    base = f"https://wa.me/{digits}" if digits else "https://wa.me/"
    return f"{base}?text={quote(text[:1800])}"


def udhar_reminder_link(shop_name, name, phone, balance):
    """Customer ko udhar yaad dilane ka WhatsApp link (wa.me, koi API nahi)."""
    text = (f"Namaste {name}, {shop_name or 'MedSync'} se yaad dilana hai ki aapka \u20b9{balance:,.2f} "
            f"udhar baaki hai. Kripya jaldi clear kar dein. Dhanyavaad!")
    return whatsapp_link(text, phone)


def po_text(po, shop):
    """Purchase Order ka plain text (email body / WhatsApp message)."""
    lines = [f"{shop.name} - Purchase Order {po.po_number}",
             f"Date: {po.date_created:%d %b %Y}",
             f"To: {po.supplier.company}", '', 'Items:']
    for n, item in enumerate(po.items.select_related('product', 'product__unit'), 1):
        unit = item.product.unit.name if item.product.unit_id else 'unit'
        lines.append(f"{n}. {item.product.name} - {item.order_qty} {unit}")
    lines += ['', 'Kripya jald se jald supply karein. Dhanyavaad.']
    if shop.phone:
        lines.append(f"Contact: {shop.phone}")
    return '\n'.join(lines)
