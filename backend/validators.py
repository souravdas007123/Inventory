"""Field checks for phone numbers, HSN codes and drug licence numbers (same idea as gstin.py).

Every check is a pure function: `normalise_*` tidies the text, `*_problem` returns '' when the value is fine
(or blank) and otherwise a short English message. A value that is already saved is only checked again when it
is edited (see `changed`), so old data never blocks a save."""
import re

# ---------------------------------------------------------------- phone
MOBILE_MSG = 'Enter a 10-digit mobile number, for example 98765 43210 (+91 or 0 in front is accepted).'
LANDLINE_MSG = 'Enter a 10-digit mobile number, or a landline with its STD code (for example 011 2345 6789).'


def _digits(raw):
    return re.sub(r'\D', '', str(raw or ''))


def normalise_phone(raw, landline=False):
    """Digits only. A leading +91 / 91 / 0 is removed from a mobile number. Blank gives ''.
    With landline=True a number that starts with 0 and is not a mobile (0 + 6-9...) keeps its STD code."""
    d = _digits(raw)
    if not d:
        return ''
    if len(d) == 12 and d.startswith('91'):
        return d[2:]
    if len(d) == 13 and d.startswith('091'):
        return d[3:]
    if len(d) == 11 and d.startswith('0') and d[1] in '6789':
        return d[1:]
    if d.startswith('0') and landline:
        return d
    return d


def phone_problem(raw, landline=False):
    """'' when fine or blank, otherwise a message. Mobile: 10 digits starting 6-9. Landline (optional): 0 + STD + number."""
    d = normalise_phone(raw, landline)
    if not d:
        return ''
    if len(d) == 10 and d[0] in '6789':
        return ''
    if landline and d.startswith('0') and 10 <= len(d) <= 12:
        return ''
    if len(d) == 10:
        return 'A mobile number must start with 6, 7, 8 or 9.'
    return LANDLINE_MSG if landline else MOBILE_MSG


# ---------------------------------------------------------------- HSN
HSN_MSG = 'HSN code must be 4, 6 or 8 digits (for example 3004 or 30049099).'


def normalise_hsn(raw):
    """Spaces, dots and dashes removed. Blank gives ''."""
    return re.sub(r'[\s.\-]+', '', str(raw or ''))


def hsn_problem(raw):
    h = normalise_hsn(raw)
    if not h:
        return ''
    if not h.isdigit():
        return 'HSN code can contain digits only (for example 3004 or 30049099).'
    if len(h) not in (4, 6, 8):
        return HSN_MSG
    return ''


# ---------------------------------------------------------------- drug licence
LICENCE_CHARS = re.compile(r'^[A-Za-z0-9 /\-.,;:()&#]+$')
LICENCE_MSG = ('Drug licence number looks wrong. Use letters, digits and / - . only, for example '
               '20/21/12345 or DL-KL-123456. Several numbers can be separated by commas.')


def normalise_licence(raw):
    """Extra spaces removed. Blank gives ''."""
    return re.sub(r'\s+', ' ', str(raw or '')).strip()


def licence_problem(raw):
    """State formats differ, so this only catches obvious mistakes: odd characters, no digit, or far too short."""
    t = normalise_licence(raw)
    if not t:
        return ''
    if not LICENCE_CHARS.match(t):
        return LICENCE_MSG
    for part in re.split(r'[,;]', t):
        part = part.strip()
        if not part:
            continue
        number = re.sub(r'(?i)^(d\.?\s?l\.?\s?(no\.?)?|licen[cs]e\s?(no\.?)?)[\s:.\-]*', '', part)
        if not re.search(r'\d', number) or len(re.sub(r'[^A-Za-z0-9]', '', number)) < 4:
            return LICENCE_MSG
    return ''


# ---------------------------------------------------------------- bill rate vs MRP
def rate_above_mrp(rate, gst_rate, mrp_per_unit):
    """True when a bill rate (before GST) is higher than the MRP of the same unit (MRP includes GST).
    One paisa of rounding is allowed because the rate is typed with two decimals."""
    from decimal import Decimal, ROUND_HALF_UP
    if rate is None or mrp_per_unit in (None, '') or Decimal(str(mrp_per_unit)) <= 0:
        return False
    limit = (Decimal(str(mrp_per_unit)) / (1 + Decimal(str(gst_rate or 0)) / 100)).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP)
    return Decimal(str(rate)) > limit + Decimal('0.01')


# ---------------------------------------------------------------- helper for models
def changed(instance, field, value, normaliser):
    """False when `value` is what is already saved in `field` (an old value is not blocked until it is edited)."""
    if not getattr(instance, 'pk', None):
        return True
    old = type(instance).objects.filter(pk=instance.pk).values_list(field, flat=True).first()
    return normaliser(old) != normaliser(value)


def check_phone(instance, field, landline=False, blank_value=None):
    """Model clean(): tidy the number, check it when new or edited. Raises ValidationError({field: message})."""
    from django.core.exceptions import ValidationError
    value = normalise_phone(getattr(instance, field), landline)
    setattr(instance, field, value or blank_value)
    if value and changed(instance, field, value, lambda v: normalise_phone(v, landline)):
        msg = phone_problem(value, landline)
        if msg:
            raise ValidationError({field: msg})
