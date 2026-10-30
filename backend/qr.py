"""A small QR code maker (no extra package): byte mode, error correction level L, versions 1 to 6 (up to 134 bytes).

Made for short texts such as a UPI payment link. `svg(text)` returns a ready-to-show SVG (dark on white, with the quiet border
scanners need) or '' when the text is too long, so a page can simply leave the picture out. The result is checked in the tests
by decoding it again with an independent decoder."""

# version: (data bytes per block, error-correction bytes per block, number of blocks)
BLOCKS = {1: (19, 7, 1), 2: (34, 10, 1), 3: (55, 15, 1), 4: (80, 20, 1), 5: (108, 26, 1), 6: (68, 18, 2)}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
MAX_BYTES = 134

_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly


def _remainder(data, degree):
    gen = _generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(degree):
            rem[i] ^= _mul(gen[i + 1], factor)
    return rem


def _pick_version(n):
    for v in sorted(BLOCKS):
        per, _, blocks = BLOCKS[v]
        if n <= per * blocks - 2:
            return v
    return None


def _codewords(raw, version):
    per, ecc, blocks = BLOCKS[version]
    total = per * blocks
    bits = [0, 1, 0, 0] + [(len(raw) >> i) & 1 for i in range(7, -1, -1)]
    for byte in raw:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, total * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < total:
        data.append(pad[(len(data) - len(bits) // 8) % 2])
    parts = [data[i * per:(i + 1) * per] for i in range(blocks)]
    ecs = [_remainder(p, ecc) for p in parts]
    out = []
    for i in range(per):
        out += [p[i] for p in parts]
    for i in range(ecc):
        out += [e[i] for e in ecs]
    return out


def _format_bits(mask):
    data = (1 << 3) | mask                      # level L = 01
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _masked(mod, func, mask):
    size = len(mod)
    rule = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
    out = [row[:] for row in mod]
    for y in range(size):
        for x in range(size):
            if not func[y][x] and rule(x, y):
                out[y][x] = not out[y][x]
    bits = _format_bits(mask)
    b = lambda i: bool((bits >> i) & 1)
    set_ = lambda x, y, v: out[y].__setitem__(x, v)
    for i in range(6):
        set_(8, i, b(i))
    set_(8, 7, b(6))
    set_(8, 8, b(7))
    set_(7, 8, b(8))
    for i in range(9, 15):
        set_(14 - i, 8, b(i))
    for i in range(8):
        set_(size - 1 - i, 8, b(i))
    for i in range(8, 15):
        set_(8, size - 15 + i, b(i))
    set_(8, size - 8, True)
    return out


def _penalty(m):
    size = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                score += 3 + (run - 5) if run >= 5 else 0
                run = 1
        score += 3 + (run - 5) if run >= 5 else 0
        text = ''.join('1' if c else '0' for c in line)
        for pat in ('10111010000', '00001011101'):
            start = text.find(pat)
            while start != -1:
                score += 40
                start = text.find(pat, start + 1)
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(1 for c in row if c) for row in m)
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
    return score


def _layout(text):
    """(modules before masking, function-pattern map, cells left over after the data) or None when the text is too long."""
    raw = text.encode('utf-8')
    version = _pick_version(len(raw))
    if version is None:
        return None
    size = 17 + 4 * version
    mod = [[False] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        if 0 <= x < size and 0 <= y < size:
            mod[y][x] = dark
            func[y][x] = True

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):          # the three big squares with their white border
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                dist = max(abs(dx), abs(dy))
                put(cx + dx, cy + dy, dist not in (2, 4))
    for i in range(size):                                          # timing lines
        if not func[6][i]:
            put(i, 6, i % 2 == 0)
        if not func[i][6]:
            put(6, i, i % 2 == 0)
    pos = ALIGN[version]
    for ay in pos:
        for ax in pos:
            if (ax, ay) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(9):                                             # room for the format bits
        func[8][i] = func[i][8] = True
    for i in range(8):
        func[8][size - 1 - i] = func[size - 1 - i][8] = True
    func[size - 8][8] = True
    data = _codewords(raw, version)
    i = 0
    spare = []
    for right in range(size - 1, 0, -2):
        if right <= 6:                    # the column pairs left of the vertical timing line are shifted by one
            right -= 1
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not func[y][x] and i < len(data) * 8:
                    mod[y][x] = bool((data[i >> 3] >> (7 - (i & 7))) & 1)
                    i += 1
                elif not func[y][x]:
                    spare.append((x, y))                           # the few remainder bits at the end are always light
    return mod, func, spare


def matrix(text):
    """The QR code of `text` as rows of True (dark) / False, or None when it is too long."""
    layout = _layout(text)
    if layout is None:
        return None
    mod, func, _ = layout
    return min((_masked(mod, func, mask) for mask in range(8)), key=_penalty)


def svg(text, pixels=240):
    """The QR code as an SVG string ('' when the text is too long)."""
    m = matrix(text)
    if m is None:
        return ''
    size, border = len(m), 4
    cells = ''.join(f'M{x + border},{y + border}h1v1h-1z' for y, row in enumerate(m) for x, c in enumerate(row) if c)
    total = size + 2 * border
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" width="{pixels}" height="{pixels}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="{total}" height="{total}" fill="#fff"/>'
            f'<path d="{cells}" fill="#000"/></svg>')
