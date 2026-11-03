"""Text must be readable on its background in BOTH the light and the dark theme (WCAG AA: 4.5 for normal text).

The colours are read from the :root variables in base.html, so changing a colour there is checked at once."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS = (Path(settings.BASE_DIR) / 'templates' / 'app' / 'base.html').read_text(encoding='utf-8')


def variables(block):
    out = {}
    for name, value in re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b', block):
        if len(value) == 4:                                  # #fff -> #ffffff
            value = '#' + ''.join(ch * 2 for ch in value[1:])
        out[name] = value
    return out


def palette():
    light = variables(re.search(r':root\{(.*?)\}', CSS, re.S).group(1))
    dark_block = re.search(r'prefers-color-scheme:dark\)\{:root\{(.*?)\}\}', CSS, re.S).group(1)
    return light, {**light, **variables(dark_block)}


def rgb(h):
    return [int(h.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)]


def lum(h):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(h)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def over(fg, bg, alpha):
    f, b = rgb(fg), rgb(bg)
    return '#%02x%02x%02x' % tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


class ContrastTests(SimpleTestCase):
    def check(self, name, text, bg, need=4.5):
        for mode, pal in zip(('light', 'dark'), palette()):
            fg = text(pal) if callable(text) else pal[text]
            back = bg(pal) if callable(bg) else pal[bg]
            got = ratio(fg, back)
            self.assertGreaterEqual(got, need, f'{name} in the {mode} theme is {got:.2f}, needs {need}')

    def test_normal_text(self):
        self.check('text on page', 'ink', 'bg')
        self.check('text on card', 'ink', 'card')
        self.check('text on the soft panel', 'ink', 'soft')
        self.check('muted text on card', 'mute', 'card')
        self.check('muted text on page', 'mute', 'bg')

    def test_primary_button_text_is_readable_at_both_ends_of_the_gradient(self):
        for end in ('btn1', 'btn2'):
            for mode, pal in zip(('light', 'dark'), palette()):
                self.assertGreaterEqual(ratio('#ffffff', pal[end]), 4.5, f'white on {end} in the {mode} theme')

    def test_secondary_button_text_and_its_outline(self):
        self.check('ghost button text on card', 'prit', 'card')
        self.check('ghost button text on hover', 'prit', 'soft')
        self.check('ghost button outline against the card', lambda p: p['prit'], 'card', need=3.0)

    def test_badges_and_messages_use_dark_enough_text_on_their_tint(self):
        for key, base in (('grn-t', '#059669'), ('red-t', '#e11d48'), ('amb-t', '#d97706'), ('blu-t', '#2563eb')):
            for mode, pal in zip(('light', 'dark'), palette()):
                got = ratio(pal[key], over(base, pal['card'], 0.15))
                self.assertGreaterEqual(got, 4.5, f'{key} badge in the {mode} theme is {got:.2f}')
        for mode, pal in zip(('light', 'dark'), palette()):
            self.assertGreaterEqual(ratio(pal['grey-t'], pal['line']), 4.5, f'grey badge in the {mode} theme')

    def test_message_boxes_are_set_apart_from_the_card(self):
        for mode, pal in zip(('light', 'dark'), palette()):
            for stripe in ('pri', 'amber', 'green', 'red'):
                self.assertGreaterEqual(ratio(pal[stripe], pal['card']), 3.0, f'{stripe} stripe in the {mode} theme')

    def test_the_old_low_contrast_pairs_are_gone(self):
        self.assertNotIn('.btn.ghost{background:var(--soft);color:var(--pri)}', CSS)
        self.assertNotIn('.msg.info,.msg.warning{background:var(--soft);color:var(--pri)}', CSS)
