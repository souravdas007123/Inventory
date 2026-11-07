"""One set of line icons for the whole screen, in place of emoji (emoji look different on every phone and computer and
do not follow the colour of the theme). Each icon is drawn in the text colour, so it works in the light and the dark theme.

    {% load icons %}  {% icon 'check' %}  {% icon n.icon %}
"""
from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

ICONS = {
    'home': '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-6h4v6"/>',
    'receipt': '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    'bottle': '<path d="M9 3h6v3H9z"/><path d="M8 6h8v14a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1z"/><path d="M8 12h8"/>',
    'package': '<path d="M12 3 3.5 7.5v9L12 21l8.5-4.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9"/>',
    'hourglass': '<path d="M7 3h10M7 21h10M8 3v3.5L12 12 8 17.5V21M16 3v3.5L12 12l4 5.5V21"/>',
    'users': '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14c2.5 0 4.5 2 4.5 4.5"/>',
    'user': '<circle cx="12" cy="8" r="3.5"/><path d="M5 21c0-3.9 3.1-7 7-7s7 3.1 7 7"/>',
    'cash': '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9.5v.01M18 14.5v.01"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'bell': '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
    'cart': '<circle cx="9" cy="20" r="1.3"/><circle cx="18" cy="20" r="1.3"/><path d="M2.5 3h3l2.2 12h11l2-8H6.2"/>',
    'pill': '<path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7z"/><path d="m8.5 8.5 7 7"/>',
    'undo': '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    'factory': '<path d="M3 21V10l6 4v-4l6 4V5h6v16z"/><path d="M7 18h2M12 18h2M17 18h2"/>',
    'clipboard': '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 11h6M9 15h6"/>',
    'refresh': '<path d="M20 11a8 8 0 0 0-14-4.5L4 9"/><path d="M4 4v5h5"/><path d="M4 13a8 8 0 0 0 14 4.5L20 15"/><path d="M20 20v-5h-5"/>',
    'scale': '<path d="M12 3v18M7 21h10M5 7h14"/><path d="m5 7-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z"/>',
    'card': '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6 15h4"/>',
    'upload': '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    'download': '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    'alert': '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.01"/>',
    'chart': '<path d="M4 20V4M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3"/>',
    'trend': '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    'calendar': '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    'briefcase': '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3 13h18"/>',
    'calculator': '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h.01M12 12h.01M16 12h.01M8 16h.01M12 16h.01M16 16h.01"/>',
    'search': '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    'truck': '<path d="M2 6h11v10H2zM13 9h4l4 3v4h-8"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
    'book': '<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z"/><path d="M5 18a2 2 0 0 1 2-2h12M9 8h6"/>',
    'trash': '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    'folder': '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    'tag': '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.2"/>',
    'ruler': '<path d="M3 16 16 3l5 5L8 21z"/><path d="m7 12 2 2M10 9l2 2M13 6l2 2"/>',
    'pulse': '<path d="M12 20S4 15 4 9a4.5 4.5 0 0 1 8-2.5A4.5 4.5 0 0 1 20 9c0 6-8 11-8 11z"/><path d="M6 12h3l1.5-3 3 6 1.5-3h3"/>',
    'save': '<path d="M5 3h11l4 4v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 3v5h7M8 21v-7h8v7"/>',
    'rocket': '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    'lifebuoy': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5"/><path d="m5.6 5.6 3.9 3.9M14.5 14.5l3.9 3.9M18.4 5.6l-3.9 3.9M9.5 14.5l-3.9 3.9"/>',
    'lock': '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    'ticket': '<path d="M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z"/><path d="M14 7v10" stroke-dasharray="2 2"/>',
    'store': '<path d="M4 9 5.5 4h13L20 9"/><path d="M4 9a2.7 2.7 0 0 0 5.3 0 2.7 2.7 0 0 0 5.4 0A2.7 2.7 0 0 0 20 9v11H4z"/><path d="M10 20v-5h4v5"/>',
    'reset': '<path d="M4 4v6h6"/><path d="M5.5 10a8 8 0 1 1-.5 5"/>',
    'printer': '<path d="M7 9V3h10v6"/><rect x="3.5" y="9" width="17" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
    'message': '<path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-8l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    'eye': '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    'check': '<path d="m5 12.5 4.5 4.5L19 7"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/>',
    'circle': '<circle cx="12" cy="12" r="9"/>',
    'help': '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.01"/>',
    'x-circle': '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    'x': '<path d="m6 6 12 12M18 6 6 18"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'pause': '<path d="M8 5v14M16 5v14"/>',
    'play': '<path d="M7 5v14l12-7z"/>',
    'pencil': '<path d="M4 20l1-4L16 5l3 3L8 19z"/><path d="m14 7 3 3"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1"/>',
    'menu': '<path d="M4 6h16M4 12h16M4 18h16"/>',
    'wrench': '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>',
    'sparkles': '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17v4M17 19h4"/>',
    'thumbs-up': '<path d="M7 11v9H4v-9zM7 11l4-8c2 0 3 1 3 3v3h5.5a1.5 1.5 0 0 1 1.5 1.8l-1.3 7a2 2 0 0 1-2 1.7H7"/>',
    'trophy': '<path d="M8 4h8v6a4 4 0 0 1-8 0zM8 6H4v1a4 4 0 0 0 4 4M16 6h4v1a4 4 0 0 1-4 4M12 14v4M8 21h8M10 18h4"/>',
    'wifi-off': '<path d="m3 3 18 18M5 12a10 10 0 0 1 5-2.6M19 12a10 10 0 0 0-3-2.2M8.5 15.5a5 5 0 0 1 4-1.4M12 19v.01"/>',
    'camera': '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    'snowflake': '<path d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9"/>',
    'arrow-left': '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    'chevron-right': '<path d="m9 6 6 6-6 6"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    'shield': '<path d="M12 3 4 6v6c0 4.5 3.2 8 8 9 4.8-1 8-4.5 8-9V6z"/>',
    'dot': '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
}

# a few more names for the same drawing
ALIASES = {'x-mark': 'x', 'tick': 'check', 'cog': 'settings', 'rupee': 'cash', 'whatsapp': 'message', 'excel': 'download'}


def svg(name, extra_class=''):
    name = ALIASES.get(name, name)
    body = ICONS.get(name)
    if body is None:
        return ''
    cls = 'ic' + (' ' + extra_class if extra_class else '')
    return format_html('<svg class="{}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{}</svg>', cls, mark_safe(body))


@register.simple_tag
def icon(name, extra_class=''):
    return svg(name, extra_class)


# The few icons the page scripts need (a label written as '@pencil Edit' becomes the icon plus the text)
JS_ICONS = ['pencil', 'undo', 'scale', 'save', 'alert', 'check', 'camera', 'hourglass', 'wifi-off', 'x', 'pill', 'search', 'plus']


@register.simple_tag
def icons_js():
    import json
    names = {n: str(svg(n)) for n in JS_ICONS}
    code = ("window.IC=" + json.dumps(names).replace('</', '<\\/') + ";window.setLabel=function(n,t){var m=/^@([\\w-]+)(?: ([\\s\\S]*))?$/.exec(t);"
            "if(!m||!window.IC[m[1]]){n.textContent=t;return}n.innerHTML=window.IC[m[1]]+(m[2]?' ':'');"
            "if(m[2])n.appendChild(document.createTextNode(m[2]))};")
    return mark_safe('<script>' + code + '</script>')


# Emoji written in Python text (button labels, hints) are turned into the same icons when the text is drawn.
EMOJI = {'⬇': 'download', '⬆': 'upload', '✅': 'check-circle', '💬': 'message', '👁': 'eye', '🖨': 'printer', '⚠': 'alert', '↩': 'undo',
         '📒': 'book', '💾': 'save', '⏳': 'hourglass', '⚖': 'scale', '🔍': 'search', '🔎': 'search', '📥': 'download', '📤': 'upload',
         '🧾': 'receipt', '💰': 'cash', '📦': 'package', '💊': 'pill', '❌': 'x-circle', '♻': 'reset', '✓': 'check', '✔': 'check',
         '📋': 'clipboard', '💸': 'cash', '🔄': 'refresh', '🔁': 'refresh', '✏': 'pencil', '✉': 'mail', '🗑': 'trash', '➕': 'plus',
         '🛒': 'cart', '📷': 'camera', '▶': 'play', '⏸': 'pause'}


@register.filter
def emoji_icons(text):
    """'🖨 Print' -> the printer icon and 'Print' (the rest of the text is escaped as usual)."""
    import re
    from django.utils.html import conditional_escape
    text = str(text).replace('\ufe0f', '')
    out, last = [], 0
    for m in re.finditer('[' + ''.join(EMOJI) + ']', text):
        out.append(conditional_escape(text[last:m.start()]))
        out.append(svg(EMOJI[m.group(0)]))
        last = m.end()
    out.append(conditional_escape(text[last:]))
    return mark_safe(''.join(out))
