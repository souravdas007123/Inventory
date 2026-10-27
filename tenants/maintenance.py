"""Maintenance mode: a time window in which every shop shows a "we are updating" page while the platform owner's pages keep working.

The window lives in a small file (`maintenance.json` next to manage.py), not in the database, so that it can be switched on
and off by the deploy script and from the server even while the database is being updated, and so that every worker process
sees the same thing. A missing, empty or damaged file means "no maintenance": a broken file can never lock the shops.

States: 'off', 'scheduled' (the window is still ahead: shops see a banner) and 'active' (inside the window: shops see the page).
The window ends by itself at its end time, so a forgotten switch cannot keep the shops out."""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)
MIN_MINUTES, MAX_MINUTES = 5, 720
MAX_MESSAGE = 200
_cache = {'sig': None, 'data': None}


def file_path():
    return getattr(settings, 'MAINTENANCE_FILE', None) or os.path.join(str(settings.BASE_DIR), 'maintenance.json')


def _parse(text):
    value = datetime.fromisoformat(str(text))
    return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)


def window():
    """{'start', 'end', 'message', 'emailed'} or None. Cheap: the file is read again only when it changes."""
    path = file_path()
    try:
        st = os.stat(path)
    except OSError:
        _cache.update(sig=None, data=None)
        return None
    sig = (st.st_mtime_ns, st.st_size)
    if _cache['sig'] == sig:
        return _cache['data']
    data = None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        data = dict(start=_parse(raw['start']), end=_parse(raw['end']), message=str(raw.get('message') or '')[:MAX_MESSAGE],
                    emailed=bool(raw.get('emailed')))
        if data['end'] <= data['start']:
            data = None
    except Exception:
        log.exception('The maintenance file %s cannot be read, so maintenance is treated as off', path)
    _cache.update(sig=sig, data=data)
    return data


def state(now=None):
    """('off' | 'scheduled' | 'active', window or None)."""
    data = window()
    now = now or timezone.now()
    if data is None or now >= data['end']:
        return 'off', None
    return ('active' if now >= data['start'] else 'scheduled'), data


def is_active(now=None):
    try:
        return state(now)[0] == 'active'
    except Exception:
        log.exception('Could not read the maintenance state')
        return False


def _write(start, end, message='', emailed=False):
    path = file_path()
    payload = dict(start=start.astimezone(dt_timezone.utc).isoformat(), end=end.astimezone(dt_timezone.utc).isoformat(),
                   message=' '.join((message or '').split())[:MAX_MESSAGE], emailed=bool(emailed))
    folder = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.maintenance-', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)                       # all at once: a reader never sees half a file
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache.update(sig=None, data=None)


def schedule(start, end, message=''):
    _write(start, end, message)


def start_now(minutes, message=''):
    now = timezone.now()
    _write(now, now + timedelta(minutes=minutes), message)


def end_now():
    """Switches maintenance off (also cancels a schedule). True when there was something to switch off."""
    try:
        os.unlink(file_path())
        found = True
    except FileNotFoundError:
        found = False
    _cache.update(sig=None, data=None)
    return found


def extend(minutes):
    """Moves the end of the current or planned window later. Returns the new end, or None when there is no window."""
    kind, data = state()
    if kind == 'off':
        return None
    end = min(data['end'] + timedelta(minutes=minutes), data['start'] + timedelta(minutes=MAX_MINUTES))
    _write(data['start'], end, data['message'], data['emailed'])
    return end


def mark_emailed():
    kind, data = state()
    if kind != 'off':
        _write(data['start'], data['end'], data['message'], True)


def clean_minutes(value):
    """(minutes, error)."""
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None, 'Enter the length of the maintenance in minutes.'
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        return None, f'The maintenance must be between {MIN_MINUTES} and {MAX_MINUTES} minutes long.'
    return minutes, ''


def clean_message(value):
    text = ' '.join((value or '').split())
    return text, ('' if len(text) <= MAX_MESSAGE else f'The message can be at most {MAX_MESSAGE} characters.')


def parse_schedule(post, now=None):
    """(start, end, message, [errors]) from the schedule form: date (YYYY-MM-DD), time (HH:MM), minutes, message.
    The time is understood in the shop time zone (settings.TIME_ZONE)."""
    now = now or timezone.now()
    errors = []
    message, problem = clean_message(post.get('message'))
    if problem:
        errors.append(problem)
    minutes, problem = clean_minutes(post.get('minutes'))
    if problem:
        errors.append(problem)
    start = None
    try:
        naive = datetime.strptime(f"{(post.get('date') or '').strip()} {(post.get('time') or '').strip()}", '%Y-%m-%d %H:%M')
        start = timezone.make_aware(naive, timezone.get_current_timezone())
    except (ValueError, TypeError, OverflowError):
        errors.append('Enter the start date and time.')
    if start is not None and start < now - timedelta(minutes=1):
        errors.append('The start time is already over. Choose a time in the future, or use "Start now".')
    if errors:
        return None, None, message, errors
    return start, start + timedelta(minutes=minutes), message, []


def fmt(value):
    """'Wed 28 Oct, 11:00 PM' in the shop time zone."""
    local = timezone.localtime(value)
    return f"{local:%a %d %b}, {local.strftime('%I:%M %p').lstrip('0')}"


def banner(now=None):
    """The line shops see before the window starts ('' when nothing is planned). Never raises."""
    try:
        kind, data = state(now)
        if kind != 'scheduled':
            return ''
        text = f"Planned maintenance: {fmt(data['start'])} to {fmt(data['end'])}. MedSync will be unavailable then, so please save your work before."
        return f"{text} {data['message']}".strip()
    except Exception:
        return ''


def platform_bar(now=None):
    """The line the platform owner sees on every platform page while a window is planned or running."""
    try:
        kind, data = state(now)
    except Exception:
        return ''
    if kind == 'active':
        return f"Maintenance is ON. Shops see the maintenance page until {fmt(data['end'])}."
    if kind == 'scheduled':
        return f"Maintenance is planned: {fmt(data['start'])} to {fmt(data['end'])}."
    return ''


def retry_after(now=None):
    """Seconds until the window ends (at least 30, at most 1 hour), for the Retry-After header."""
    kind, data = state(now)
    if kind != 'active':
        return 60
    return int(max(30, min(3600, (data['end'] - (now or timezone.now())).total_seconds())))
