"""Helpers for sending alerts (low stock / expiry) and Purchase Orders by email / WhatsApp."""
import re
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone

from . import tenancy
from .models import Batch, Product, ShopProfile

MAX_LINES = 25      # email
WA_LINES = 6        # WhatsApp: a few lines per section keep the message (and the wa.me link) short
WA_CHARS = 1400


def collect_alerts(days=30, watch_days=90):
    """Low stock, out of stock, expiring soon (within `days`), expiring later (up to `watch_days`) and expired batches."""
    today = timezone.localdate()
    active = Product.objects.exclude(is_active=False).select_related('unit')
    live = (Batch.objects.filter(qty__gt=0, expire_date__isnull=False)
            .select_related('product', 'unit', 'supplier', 'purchase_item'))
    return dict(
        today=today, days=days, watch_days=watch_days,
        out=list(active.filter(stock_qty=0).order_by('name')),
        low=list(active.filter(stock_qty__gt=0, stock_qty__lte=F('reorder_level')).order_by('stock_qty', 'name')),
        expiring=list(live.filter(expire_date__gte=today, expire_date__lte=today + timedelta(days=days))
                      .order_by('expire_date')),
        watch=list(live.filter(expire_date__gt=today + timedelta(days=days),
                               expire_date__lte=today + timedelta(days=watch_days)).order_by('expire_date')),
        expired=list(live.filter(expire_date__lt=today).order_by('expire_date')),
    )


def batch_value(b):
    """What the batch cost us (price paid per base unit x qty left). None when the purchase is unknown."""
    item = b.purchase_item
    if item is None or not item.purchase_price or not item.base_qty:
        return None
    return Decimal(item.purchase_price) / Decimal(item.base_qty) * b.qty


def _total_value(batches):
    return sum((v for v in (batch_value(b) for b in batches) if v), Decimal('0'))


def _money(x):
    return f"Rs {Decimal(x):,.0f}"


def _batch_line(b):
    """'Dolo 650 | B-101 | 20 tab | exp 20 Nov 26 | Rack A, Row 2 | Sup: ABC' - what the staff needs to find and pull it."""
    unit = b.unit.name if b.unit_id else ''
    where = ', '.join(x for x in (f"Rack {b.rack}" if b.rack else '', f"Row {b.row}" if b.row else '', b.location or '') if x)
    bits = [b.product.name if b.product else '-', b.batch_number or 'no batch no.', f"{b.qty} {unit}".strip(),
            f"exp {b.expire_date:%d %b %y}", where or 'place not set']
    if b.supplier_id:
        bits.append(f"Sup: {b.supplier.company}")
    return ' | '.join(bits)


def _section(title, lines, total, limit=MAX_LINES, note=''):
    if not total:
        return []
    out = [f"{title} ({total}){note}"]
    out += [f"  - {line}" for line in lines[:limit]]
    if total > limit:
        out.append(f"  ...and {total - limit} more (see the app)")
    return out + ['']


def alert_digest_text(alerts, shop_name='', wa=False, limit=MAX_LINES, max_chars=None):
    """Plain-text digest. For WhatsApp (wa=True) it is short: a few lines per section, bold headings, emoji."""
    bold = (lambda t: f"*{t}*") if wa else (lambda t: t)
    icon = (lambda e: e + ' ') if wa else (lambda e: '')
    head = f"{shop_name or 'MedSync'} - Stock alerts ({alerts['today']:%d %b %Y})"
    parts = [bold(head), '']
    expired, soon, watch = alerts['expired'], alerts['expiring'], alerts.get('watch', [])
    risk = [(_total_value(expired), 'already expired'), (_total_value(soon), f"expiring in {alerts['days']} days")]
    risk = [f"{_money(v)} {t}" for v, t in risk if v]
    if risk:
        parts += [f"Money at risk: {', '.join(risk)}", '']
    parts += _section(bold(icon('🔴') + 'EXPIRED - take off the shelf'), [_batch_line(b) for b in expired], len(expired), limit)
    parts += _section(bold(icon('🟠') + f"EXPIRING within {alerts['days']} days"), [_batch_line(b) for b in soon], len(soon), limit)
    parts += _section(bold(icon('🟡') + f"EXPIRING in {alerts['days'] + 1}-{alerts['watch_days']} days (ask supplier to take back)"),
                      [_batch_line(b) for b in watch], len(watch), min(limit, 4) if wa else limit)
    parts += _section(bold(icon('⛔') + 'OUT OF STOCK'), [p.name or str(p) for p in alerts['out']], len(alerts['out']), limit)
    parts += _section(bold(icon('📉') + 'LOW STOCK'), [f"{p.name} ({p.stock_display()})" for p in alerts['low']],
                      len(alerts['low']), limit)
    if len(parts) == 2:
        parts.append('All good - no alerts.')
    text = '\n'.join(parts).strip()
    if max_chars and len(text) > max_chars:                    # never cut in the middle of a line
        lines = text[:max_chars].rsplit('\n', 1)[0].split('\n')
        while lines and (not lines[-1].strip() or re.search(r'\(\d+\)$', lines[-1].strip())):   # no empty / orphan heading at the end
            lines.pop()
        text = '\n'.join(lines) + '\n...(more in the app)'
    return text


def alert_recipients():
    # DJANGO_ALERT_EMAILS belongs to the main shop only. With many shops, another shop's sales must never go to it.
    emails = list(getattr(settings, 'ALERT_EMAILS', []) or []) if tenancy.is_platform() else []
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
    count = len(alerts['out']) + len(alerts['low']) + len(alerts['expired']) + len(alerts['expiring']) + len(alerts['watch'])
    sent = send_mail(f"[{shop.name}] Stock alerts - {count} item(s)", text,
                     settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
    return sent, recipients


def whatsapp_link(text, phone=''):
    """wa.me link (no API needed). A 10-digit number is assumed to be an Indian (+91) number."""
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) == 10:
        digits = '91' + digits
    base = f"https://wa.me/{digits}" if digits else "https://wa.me/"
    if len(text) > 1500:                                   # a long message makes the link too long for WhatsApp
        text = text[:1500].rsplit('\n', 1)[0] + '\n...'
    return f"{base}?text={quote(text)}"


def credit_reminder_link(shop_name, name, phone, balance):
    """WhatsApp link (wa.me, no API) to remind a customer about outstanding credit."""
    text = (f"Hello {name}, this is a reminder from {shop_name or 'MedSync'} that your outstanding balance of \u20b9{balance:,.2f} "
            f"is due. Please clear it at your earliest convenience. Thank you!")
    return whatsapp_link(text, phone)


def refill_reminder_link(shop_name, name, phone, medicines):
    """WhatsApp link (wa.me, no API) to remind a regular customer that a medicine may be finishing."""
    meds = ', '.join(medicines)
    text = (f"Hello {name}, this is {shop_name or 'MedSync'}. Your {meds} may be finishing around now. "
            f"Shall we keep it ready for you, or send it to your home? Thank you!")
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


def alerts_whatsapp_text(shop_name=''):
    """The short WhatsApp version of the stock alerts (built only when the button is pressed)."""
    return alert_digest_text(collect_alerts(), shop_name, wa=True, limit=WA_LINES, max_chars=WA_CHARS)
