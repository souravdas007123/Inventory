"""Sign-up e-mails: the confirmation link and the welcome letter. Each goes out as plain text plus a neat HTML version.

Every value that a person typed (shop name, owner name) is escaped in the HTML. The password is never put in an e-mail."""
import logging
from datetime import date

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

from .reminders import _contact_lines, shop_address

log = logging.getLogger(__name__)
BRAND = 'MedSync'
ACCENT = '#0f766e'


def _html(preheader, title, intro, blocks, button=None, small=''):
    """blocks: [('p', text) | ('steps', [text, ...]) | ('box', [(label, value), ...])]. All text is plain and escaped here."""
    body = []
    for kind, data in blocks:
        if kind == 'p':
            body.append(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#334155">{escape(data)}</p>')
        elif kind == 'steps':
            items = ''.join(
                f'<tr><td valign="top" style="padding:0 12px 10px 0"><span style="display:inline-block;width:24px;height:24px;line-height:24px;'
                f'text-align:center;border-radius:12px;background:{ACCENT};color:#fff;font-size:13px;font-weight:bold">{i}</span></td>'
                f'<td style="padding:0 0 10px;font-size:15px;line-height:1.5;color:#334155">{escape(t)}</td></tr>'
                for i, t in enumerate(data, 1))
            body.append(f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 10px">{items}</table>')
        elif kind == 'box':
            rows = ''.join(f'<tr><td style="padding:4px 12px 4px 0;font-size:14px;color:#64748b">{escape(a)}</td>'
                           f'<td style="padding:4px 0;font-size:14px;color:#0f172a;font-weight:bold">{escape(b)}</td></tr>' for a, b in data)
            body.append(f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 18px;padding:12px 16px;'
                        f'background:#f1f5f9;border-radius:8px;width:100%">{rows}</table>')
    btn = ''
    if button:
        text, url = button
        btn = (f'<p style="margin:6px 0 20px"><a href="{escape(url)}" style="display:inline-block;background:{ACCENT};color:#ffffff;'
               f'text-decoration:none;font-weight:bold;font-size:16px;padding:13px 26px;border-radius:8px">{escape(text)}</a></p>'
               f'<p style="margin:0 0 18px;font-size:12px;color:#64748b;word-break:break-all">If the button does not work, copy this link: '
               f'<a href="{escape(url)}" style="color:{ACCENT}">{escape(url)}</a></p>')
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f8fafc">'
        f'<div style="display:none;max-height:0;overflow:hidden;color:#f8fafc">{escape(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;'
        'border:1px solid #e2e8f0;font-family:Arial,Helvetica,sans-serif">'
        f'<tr><td style="padding:20px 28px;background:{ACCENT};border-radius:12px 12px 0 0;color:#ffffff;font-size:20px;font-weight:bold">{BRAND}</td></tr>'
        f'<tr><td style="padding:28px"><h1 style="margin:0 0 14px;font-size:22px;color:#0f172a">{escape(title)}</h1>'
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#334155">{escape(intro)}</p>'
        + ''.join(body) + btn +
        (f'<p style="margin:0;font-size:13px;line-height:1.5;color:#64748b">{escape(small)}</p>' if small else '') +
        '</td></tr>'
        f'<tr><td style="padding:16px 28px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8">{BRAND} - pharmacy billing and inventory. '
        'You got this e-mail because someone used this address to sign up.</td></tr>'
        '</table></td></tr></table></body></html>')


def _text(title, intro, blocks, button=None, small=''):
    lines = [title, '', intro, '']
    for kind, data in blocks:
        if kind == 'p':
            lines += [data, '']
        elif kind == 'steps':
            lines += [f'{i}. {t}' for i, t in enumerate(data, 1)] + ['']
        elif kind == 'box':
            lines += [f'{a}: {b}' for a, b in data] + ['']
    if button:
        lines += [f'{button[0]}:', button[1], '']
    if small:
        lines += [small, '']
    lines += ['Thank you,', f'Team {BRAND}']
    return '\n'.join(lines)


def build(preheader, title, intro, blocks, button=None, small=''):
    return _text(title, intro, blocks, button, small), _html(preheader, title, intro, blocks, button, small)


def send(subject, text, html, to):
    """Sends text + HTML. Raises when the mail server refuses, so the caller can decide what that means."""
    msg = EmailMultiAlternatives(subject, text, None, [to])
    msg.attach_alternative(html, 'text/html')
    msg.send(fail_silently=False)


def confirm_message(pending, link, valid_hours):
    """(subject, text, html) of the sign-up confirmation. The link sits on a line of its own."""
    days = settings.TRIAL_DAYS
    text, html = build(
        'One click to create your shop.',
        'Please confirm your e-mail address',
        f'Hello {pending.owner_name}, thank you for choosing {BRAND}. Confirm your e-mail to create your shop "{pending.shop_name}". '
        f'Your {days}-day free trial starts as soon as it is ready.',
        [('p', f'Nothing is charged and no card is needed. This link works for {valid_hours} hours.')],
        button=('Confirm my e-mail', link),
        small='If you did not sign up, you can safely ignore this e-mail. Nothing will be created.')
    return 'Confirm your MedSync account', text, html


def welcome_message(shop, login_email, today=None):
    """(subject, text, html) of the welcome letter. Never contains the password."""
    today = today or date.today()
    address = shop_address(shop)
    ends = f'{shop.trial_ends:%d %b %Y}' if shop.trial_ends else ''
    name = shop.owner_name or 'there'
    box = [('Your shop', shop.name)]
    if address:
        box.append(('Web address', address))
    box.append(('Sign in with', login_email))
    if ends:
        box.append(('Free trial until', ends))
    steps = ['After you sign in, open "Setup guide" in the menu. It lists what to enter and import first, in the right order.',
             'Add your medicines and suppliers (bring them in at once with Import / Export).',
             'Enter your opening stock as a purchase, so each batch and its expiry date is tracked.',
             'Make your first bill from Billing. Print it or send it to the customer on WhatsApp.',
             'Open Sign-in security and turn on 2-step sign-in to keep your shop safe.']
    contact = [c for c in _contact_lines() if not c.startswith('Please contact')]
    help_line = ('Stuck anywhere? Open "Support tickets" inside your shop and write to us, we reply there and by e-mail.'
                 + (' You can also reach us here: ' + '; '.join(contact) if contact else ''))
    text, html = build(
        f'Your shop {shop.name} is ready.',
        'Welcome to MedSync, your shop is ready',
        f'Hello {name}, your shop "{shop.name}" has been created and your free trial has started. Here is everything you need to begin.',
        [('box', box), ('p', 'Your password is the one you chose while signing up. We never e-mail passwords.'),
         ('p', 'Get going in five steps:'), ('steps', steps), ('p', help_line),
         ('p', 'Tip: open your shop on your phone and choose "Add to Home screen" to use it like an app, including the camera barcode scan.')],
        button=('Open my shop', address + 'login/') if address else None,
        small='Your data belongs to you. You can download a full copy any time from the Backup page.')
    return f'Welcome to MedSync, {shop.name} is ready', text, html


def send_welcome(shop, login_email):
    """Best effort: a failed welcome letter must never undo or hide a shop that was made. Returns True when it was sent."""
    if getattr(settings, 'SIGNUP_SKIP_EMAIL', False):
        return False
    try:
        subject, text, html = welcome_message(shop, login_email)
        send(subject, text, html, login_email)
        return True
    except Exception:
        log.exception('Could not send the welcome e-mail for %s', shop.schema_name)
        return False


def maintenance_message(shop, window, now=None):
    """(subject, text, html): the shop owner is told about a planned or a running maintenance. No password, no links to act on."""
    from django.utils import timezone

    from . import maintenance
    now = now or timezone.now()
    running = window['start'] <= now
    name = shop.owner_name or 'there'
    box = [('Until', maintenance.fmt(window['end']))] if running else [('From', maintenance.fmt(window['start'])), ('Until', maintenance.fmt(window['end']))]
    blocks = [('box', box), ('p', 'Your data is safe and nothing needs to be done on your side. During this time your shop shows a short update page. '
                                  'It opens again by itself when we are done.')]
    if not running:
        blocks.append(('p', 'Please finish your bills and save your work before it starts.'))
    if window.get('message'):
        blocks.append(('p', window['message']))
    if running:
        subject, title = 'MedSync is being updated', 'MedSync is being updated right now'
        intro = f'Hello {name}, we are updating MedSync. Your shop "{shop.name}" will be back by {maintenance.fmt(window["end"])}.'
    else:
        subject, title = f'Planned maintenance: {maintenance.fmt(window["start"])}', 'Planned maintenance'
        intro = f'Hello {name}, we will update MedSync soon, to make it better and safer. Your shop "{shop.name}" will be briefly unavailable.'
    text, html = build(f'{subject}.', title, intro, blocks)
    return subject, text, html
