"""GSTIN check: 15 characters = state code (2 digits) + PAN (10) + entity number (1) + 'Z' + a check character.
The last character is calculated from the first 14, so a typing mistake or a random number is caught.
This tells that a number has a valid shape; it cannot tell whether it is registered on the GST portal."""
import re

CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
SHAPE = re.compile(r'^(\d{2})[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')


def normalise(raw):
    """Capital letters, no spaces or dashes. None / blank gives ''."""
    return re.sub(r'[\s\-]+', '', str(raw or '')).upper()


def check_char(first14):
    """The check character for the first 14 characters of a GSTIN."""
    total = 0
    for i, ch in enumerate(first14):
        v = CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return CHARS[(36 - total % 36) % 36]


def problem(raw, state_codes):
    """'' when the GSTIN is fine (or blank), otherwise a short message for the user."""
    g = normalise(raw)
    if not g:
        return ''
    if len(g) != 15:
        return 'GSTIN must be 15 characters.'
    m = SHAPE.match(g)
    if not m:
        return 'GSTIN is not valid. It looks like 19ABCDE1234F1Z5 (state code, PAN, entity number, Z, check character).'
    if m.group(1) not in state_codes:
        return f'GSTIN starts with {m.group(1)}, which is not a GST state code.'
    if check_char(g[:14]) != g[14]:
        return 'GSTIN is not valid: the last character does not match the rest. Please check it for a typing mistake.'
    return ''


def changed(instance, value):
    """False when `value` is what is already saved on this record (an old number is not blocked until it is edited)."""
    if not getattr(instance, 'pk', None):
        return True
    old = type(instance).objects.filter(pk=instance.pk).values_list('gstin', flat=True).first()
    return normalise(old) != normalise(value)
