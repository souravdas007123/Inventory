"""EAN-13 / UPC-A / EAN-8 barcodes for product labels (no extra package).

Every scanner (USB and the phone camera) reads EAN-13. A shop code made here starts with "2": numbers beginning with 2 are
reserved worldwide for in-store use, so they can never clash with a manufacturer's barcode.
`svg(code)` returns a ready-to-print SVG, or '' when the text is not a valid 13-digit EAN-13 (wrong length or check digit).
The result is decoded again by an independent decoder in the tests."""
from html import escape

L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011']
G = ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111']
R = [''.join('1' if c == '0' else '0' for c in x) for x in L]
PARITY = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL']


def check_digit(first12):
    """The 13th digit of an EAN-13 from its first 12 digits."""
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(first12))
    return str((10 - s % 10) % 10)


def is_valid(code):
    c = str(code or '').strip()
    return len(c) == 13 and c.isdigit() and c[12] == check_digit(c[:12])


def check_digit8(first7):
    """The 8th digit of an EAN-8 from its first 7 digits."""
    s = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(first7))
    return str((10 - s % 10) % 10)


def kind(code):
    """'ean13', 'upca' (12 digits, drawn as an EAN-13 with a leading 0), 'ean8', or '' when the code cannot be drawn."""
    c = str(code or '').strip()
    if not c.isdigit():
        return ''
    if len(c) == 13 and c[12] == check_digit(c[:12]):
        return 'ean13'
    if len(c) == 12 and ('0' + c)[12] == check_digit('0' + c[:11]):
        return 'upca'
    if len(c) == 8 and c[7] == check_digit8(c[:7]):
        return 'ean8'
    return ''


def shop_code(n):
    """The in-store EAN-13 for number n (a product id): '2' + 11 digits + check digit."""
    n = int(n)
    if n < 0 or n > 99999999999:
        raise ValueError('number out of range')
    first12 = '2' + str(n).zfill(11)
    return first12 + check_digit(first12)


def modules(code):
    """The 95 bars / spaces ('1' = bar) of a valid EAN-13."""
    if not is_valid(code):
        raise ValueError('not a valid EAN-13')
    c = str(code).strip()
    pat = PARITY[int(c[0])]
    left = ''.join((L if p == 'L' else G)[int(d)] for p, d in zip(pat, c[1:7]))
    right = ''.join(R[int(d)] for d in c[7:])
    return '101' + left + '01010' + right + '101'


def modules8(code):
    c = str(code).strip()
    return '101' + ''.join(L[int(d)] for d in c[:4]) + '01010' + ''.join(R[int(d)] for d in c[4:]) + '101'


def svg(code, height=60, text=True):
    """The barcode as an SVG (scales to the width it is placed in); '' when `code` is not a valid EAN-13 / UPC-A / EAN-8."""
    k = kind(code)
    if not k:
        return ''
    c = str(code).strip()
    if k == 'ean8':
        return _svg8(c, height, text)
    if k == 'upca':
        c = '0' + c
    m = modules(c)
    quiet = 9
    width = 95 + 2 * quiet
    guard = {0, 1, 2, 45, 46, 47, 48, 49, 92, 93, 94}           # the guard bars reach a little lower, like on printed packs
    bars = []
    i = 0
    while i < 95:
        if m[i] == '1':
            j = i
            while j < 95 and m[j] == '1':
                j += 1
            h = height + (3 if text and i in guard else 0)
            bars.append(f'<rect x="{quiet + i}" y="0" width="{j - i}" height="{h}"/>')
            i = j
        else:
            i += 1
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height + (16 if text else 0)}" '
           f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="Barcode {escape(c)}">',
           f'<rect width="{width}" height="{height + (16 if text else 0)}" fill="#fff"/>', '<g fill="#000">'] + bars + ['</g>']
    if text:
        y = height + 13
        out.append('<g font-family="Arial, Helvetica, sans-serif" font-size="10" fill="#000" text-anchor="middle">')
        out.append(f'<text x="{quiet - 4}" y="{y}">{c[0]}</text>')
        out.append(f'<text x="{quiet + 3 + 21}" y="{y}" textLength="40" lengthAdjust="spacing">{c[1:7]}</text>')
        out.append(f'<text x="{quiet + 50 + 21}" y="{y}" textLength="40" lengthAdjust="spacing">{c[7:]}</text>')
        out.append('</g>')
    out.append('</svg>')
    return ''.join(out)


def _svg8(c, height, text):
    m = modules8(c)
    quiet, n = 7, 67
    width = n + 2 * quiet
    guard = {0, 1, 2, 31, 32, 33, 34, 35, 64, 65, 66}
    bars, i = [], 0
    while i < n:
        if m[i] == '1':
            j = i
            while j < n and m[j] == '1':
                j += 1
            h = height + (3 if text and i in guard else 0)
            bars.append(f'<rect x="{quiet + i}" y="0" width="{j - i}" height="{h}"/>')
            i = j
        else:
            i += 1
    total = height + (16 if text else 0)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total}" preserveAspectRatio="xMidYMid meet" '
           f'role="img" aria-label="Barcode {escape(c)}">', f'<rect width="{width}" height="{total}" fill="#fff"/>', '<g fill="#000">'] + bars + ['</g>']
    if text:
        y = height + 13
        out.append('<g font-family="Arial, Helvetica, sans-serif" font-size="10" fill="#000" text-anchor="middle">')
        out.append(f'<text x="{quiet + 3 + 14}" y="{y}" textLength="26" lengthAdjust="spacing">{c[:4]}</text>')
        out.append(f'<text x="{quiet + 36 + 14}" y="{y}" textLength="26" lengthAdjust="spacing">{c[4:]}</text>')
        out.append('</g>')
    out.append('</svg>')
    return ''.join(out)
