"""Small illustrations for blog articles, drawn as inline SVG (no image files to host, nothing to download, always sharp).

The drawings are fixed constants written here, never built from user text, so they are safe to put in a page.
An article can still use a real picture instead: see BlogPost.image_url.
"""
from django.utils.safestring import mark_safe

INK, PANEL, AMBER, CREAM, GREEN, RED, MIST = '#0a2f2a', '#12453d', '#d98324', '#fffef9', '#4fd1a5', '#e05a47', '#9ec7bd'


def _lines(x, y, n, w, gap=22, color=MIST):
    return ''.join(f'<rect x="{x}" y="{y + i * gap}" width="{w - (i % 3) * 28}" height="8" rx="4" fill="{color}"/>' for i in range(n))


def _expiry():
    cells = ''.join(f'<circle cx="{150 + c * 44}" cy="{190 + r * 42}" r="6" fill="{MIST}"/>' for r in range(4) for c in range(5))
    pills = ''.join(f'<rect x="{500 + i * 52}" y="{238}" width="38" height="70" rx="19" fill="#fff" stroke="{MIST}" stroke-width="3"/>' for i in range(4))
    return (f'<rect x="110" y="70" width="290" height="290" rx="18" fill="{CREAM}"/><rect x="110" y="70" width="290" height="62" rx="18" fill="{AMBER}"/>'
            f'<rect x="110" y="110" width="290" height="22" fill="{AMBER}"/>{cells}<circle cx="326" cy="274" r="24" fill="none" stroke="{RED}" stroke-width="6"/>'
            f'<rect x="470" y="218" width="250" height="110" rx="14" fill="#cfe0dc" transform="rotate(-6 595 273)"/>'
            f'<g transform="rotate(-6 595 273)">{pills}</g>'
            f'<g transform="rotate(5 595 150)"><rect x="500" y="120" width="190" height="56" rx="10" fill="{RED}"/>'
            f'<text x="595" y="157" text-anchor="middle" font-size="26" font-weight="800" fill="#fff" font-family="ui-monospace,Consolas,monospace">EXP 03/27</text></g>')


def _gst():
    return (f'<rect x="120" y="50" width="250" height="320" rx="6" fill="{CREAM}"/>'
            f'<text x="245" y="92" text-anchor="middle" font-size="20" font-weight="800" fill="{INK}" font-family="ui-monospace,Consolas,monospace">TAX INVOICE</text>'
            f'{_lines(150, 120, 6, 190, 26, "#9aa9a5")}<rect x="150" y="300" width="190" height="3" fill="{INK}"/>'
            f'<text x="150" y="340" font-size="22" font-weight="800" fill="{INK}" font-family="ui-monospace,Consolas,monospace">TOTAL</text>'
            f'<text x="340" y="340" text-anchor="end" font-size="22" font-weight="800" fill="{INK}" font-family="ui-monospace,Consolas,monospace">₹234</text>'
            f'<circle cx="560" cy="170" r="92" fill="{AMBER}"/><text x="560" y="205" text-anchor="middle" font-size="110" font-weight="800" fill="{INK}" font-family="Georgia,serif">%</text>'
            f'<rect x="470" y="290" width="76" height="40" rx="20" fill="{PANEL}" stroke="{MIST}" stroke-width="2"/><text x="508" y="317" text-anchor="middle" font-size="18" font-weight="700" fill="{CREAM}" font-family="system-ui,sans-serif">B2B</text>'
            f'<rect x="562" y="290" width="76" height="40" rx="20" fill="{PANEL}" stroke="{MIST}" stroke-width="2"/><text x="600" y="317" text-anchor="middle" font-size="18" font-weight="700" fill="{CREAM}" font-family="system-ui,sans-serif">B2C</text>'
            f'<rect x="654" y="290" width="76" height="40" rx="20" fill="{PANEL}" stroke="{MIST}" stroke-width="2"/><text x="692" y="317" text-anchor="middle" font-size="18" font-weight="700" fill="{CREAM}" font-family="system-ui,sans-serif">HSN</text>')


def _register():
    return (f'<path d="M90 90 Q250 60 400 100 V350 Q250 312 90 340 Z" fill="{CREAM}"/><path d="M710 90 Q550 60 400 100 V350 Q550 312 710 340 Z" fill="#f1efe6"/>'
            f'<rect x="396" y="98" width="8" height="252" fill="{MIST}"/>'
            f'<text x="165" y="190" font-size="92" font-weight="800" fill="{RED}" font-family="Georgia,serif" font-style="italic">Rx</text>'
            f'{_lines(130, 230, 4, 220, 26, "#9aa9a5")}{_lines(450, 130, 8, 220, 26, "#9aa9a5")}'
            f'<g transform="rotate(-8 600 300)"><rect x="520" y="268" width="150" height="64" rx="8" fill="none" stroke="{RED}" stroke-width="5"/>'
            f'<text x="595" y="314" text-anchor="middle" font-size="40" font-weight="800" fill="{RED}" font-family="ui-monospace,Consolas,monospace">H1</text></g>')


def _fefo():
    dates = ['03/27', '08/27', '11/27', '02/28', '06/28']
    boxes = ''.join(f'<rect x="{90 + i * 124}" y="{150 - (i % 2) * 8}" width="104" height="{140 + (i % 2) * 8}" rx="8" fill="{CREAM if i else AMBER}" opacity="{1 - i * 0.12:.2f}"/>'
                    f'<text x="{142 + i * 124}" y="{232}" text-anchor="middle" font-size="22" font-weight="800" fill="{INK}" font-family="ui-monospace,Consolas,monospace">{d}</text>'
                    for i, d in enumerate(dates))
    return (f'{boxes}<rect x="70" y="290" width="660" height="14" rx="7" fill="{MIST}"/>'
            f'<path d="M142 120 V70" stroke="{GREEN}" stroke-width="8" stroke-linecap="round"/><path d="M122 92 L142 66 L162 92" fill="none" stroke="{GREEN}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<text x="190" y="96" font-size="26" font-weight="800" fill="{GREEN}" font-family="system-ui,sans-serif">sold first</text>')


def _prices():
    bars = [('A', 230, CREAM), ('B', 175, GREEN), ('C', 205, CREAM)]
    out = ''.join(f'<rect x="{150 + i * 170}" y="{330 - h}" width="110" height="{h}" rx="10" fill="{c}"/>'
                  f'<text x="{205 + i * 170}" y="{362}" text-anchor="middle" font-size="22" font-weight="700" fill="{CREAM}" font-family="system-ui,sans-serif">{n}</text>'
                  for i, (n, h, c) in enumerate(bars))
    return (f'<rect x="100" y="332" width="600" height="6" rx="3" fill="{MIST}"/>{out}'
            f'<circle cx="375" cy="130" r="38" fill="{AMBER}"/><text x="375" y="145" text-anchor="middle" font-size="42" font-weight="800" fill="{INK}" font-family="Georgia,serif">₹</text>'
            f'<path d="M375 172 V196" stroke="{AMBER}" stroke-width="6" stroke-linecap="round"/>'
            f'<path d="M520 110 l30 40 l-14 0 l0 30 l-32 0 l0 -30 l-14 0 z" fill="{RED}" transform="translate(70 -10) scale(.9)"/>')


def _import():
    grid = ''.join(f'<rect x="{100 + c * 72}" y="{110 + r * 46}" width="68" height="42" fill="{CREAM if r else "#cfe0dc"}" stroke="#9aa9a5" stroke-width="2"/>' for r in range(5) for c in range(3))
    return (f'{grid}<path d="M335 215 H440" stroke="{AMBER}" stroke-width="10" stroke-linecap="round"/><path d="M416 188 L446 215 L416 242" fill="none" stroke="{AMBER}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<rect x="480" y="90" width="230" height="250" rx="14" fill="{CREAM}"/><rect x="480" y="90" width="230" height="40" rx="14" fill="{AMBER}"/><rect x="480" y="116" width="230" height="14" fill="{AMBER}"/>'
            f'{_lines(504, 156, 5, 180, 34, "#9aa9a5")}<circle cx="672" cy="304" r="20" fill="{GREEN}"/><path d="M662 304 l8 9 l14 -17" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')


COVERS = {
    'expiry': ('Expiry', _expiry),
    'gst': ('GST', _gst),
    'register': ('Drug register', _register),
    'fefo': ('FEFO', _fefo),
    'prices': ('Supplier prices', _prices),
    'import': ('Excel import', _import),
}
CHOICES = [('', 'None')] + [(k, v[0]) for k, v in COVERS.items()]


def cover_svg(key, label='', decorative=False):
    """The illustration for `key` as a ready-to-use <svg>. Returns '' for an unknown key."""
    item = COVERS.get(key)
    if not item:
        return ''
    access = 'aria-hidden="true"' if decorative else f'role="img" aria-label="{"Illustration: " + item[0]}"'
    return mark_safe(f'<svg viewBox="0 0 800 420" xmlns="http://www.w3.org/2000/svg" {access} preserveAspectRatio="xMidYMid slice">'
                     f'<rect width="800" height="420" fill="{INK}"/><circle cx="700" cy="40" r="170" fill="{PANEL}"/><circle cx="60" cy="400" r="120" fill="{PANEL}"/>'
                     f'{item[1]()}</svg>')
