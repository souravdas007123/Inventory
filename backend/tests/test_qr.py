"""The small QR code maker. Where OpenCV is installed the result is compared with OpenCV's own QR encoder."""
import random
import string
from unittest import skipUnless

from django.test import SimpleTestCase

from backend import qr

try:
    import cv2
    import numpy as np
    HAS_CV2 = hasattr(cv2, 'QRCodeEncoder')
except Exception:                                            # not installed: only the self-checks run
    HAS_CV2 = False

UPI = 'upi://pay?pa=sourav@okhdfcbank&pn=MedSync&am=500.00&cu=INR&tn=MedSync monthly MS-12'


class StructureTests(SimpleTestCase):
    def test_sizes_follow_the_text_length(self):
        for n, size in ((1, 21), (17, 21), (18, 25), (32, 25), (33, 29), (53, 29), (54, 33), (78, 33), (79, 37), (106, 37), (107, 41), (134, 41)):
            self.assertEqual(len(qr.matrix('a' * n)), size, n)

    def test_too_long_gives_nothing(self):
        self.assertIsNone(qr.matrix('a' * 135))
        self.assertEqual(qr.svg('a' * 135), '')

    def test_the_three_big_squares_and_the_timing_lines(self):
        for text in ('a', UPI):
            m = qr.matrix(text)
            n = len(m)
            for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        self.assertEqual(m[cy + dy][cx + dx], max(abs(dx), abs(dy)) != 2, (text, cx, cy, dx, dy))
            for i in range(8, n - 8):
                self.assertEqual((m[6][i], m[i][6]), (i % 2 == 0, i % 2 == 0))

    def test_the_format_bits_are_valid_and_the_same_in_both_places(self):
        for text in ('a', UPI, 'x' * 134):
            m = qr.matrix(text)
            n = len(m)
            first = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
            second = [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]
            self.assertEqual(first, second)
            bits = sum(int(b) << i for i, b in enumerate(first)) ^ 0x5412
            data = bits >> 10
            rem = data
            for _ in range(10):
                rem = (rem << 1) ^ ((rem >> 9) * 0x537)
            self.assertEqual(bits, (data << 10) | rem)             # a valid BCH word
            self.assertEqual(data >> 3, 1)                         # error correction level L
            self.assertTrue(m[n - 8][8])                           # the always-dark module

    def test_the_code_words_have_a_zero_error_remainder(self):
        random.seed(3)
        for n in (5, 30, 50, 100):
            raw = bytes(random.choice(b'abcdef') for _ in range(n))
            v = qr._pick_version(n)
            per, ecc, blocks = qr.BLOCKS[v]
            words = qr._codewords(raw, v)
            self.assertEqual(len(words), (per + ecc) * blocks)
            for b in range(blocks):                                # take each block apart again
                block = [words[i * blocks + b] for i in range(per)] + [words[per * blocks + i * blocks + b] for i in range(ecc)]
                for i in range(ecc):
                    s = 0
                    for c in block:
                        s = qr._mul(s, qr._EXP[i]) ^ c
                    self.assertEqual(s, 0, (n, b, i))

    def test_the_same_text_always_gives_the_same_picture(self):
        self.assertEqual(qr.svg(UPI), qr.svg(UPI))
        self.assertNotEqual(qr.svg(UPI), qr.svg(UPI + 'x'))

    def test_the_svg_is_well_formed(self):
        from xml.etree import ElementTree
        root = ElementTree.fromstring(qr.svg(UPI))
        self.assertTrue(root.tag.endswith('svg'))
        self.assertEqual(root.get('viewBox'), '0 0 45 45')         # 37 modules + 4 border on each side


@skipUnless(HAS_CV2, 'OpenCV is not installed')
class ReferenceTests(SimpleTestCase):
    """Every module except the unused remainder bits must equal what OpenCV's own encoder makes (for some mask)."""

    def test_matches_the_reference_encoder_for_all_versions(self):
        params = cv2.QRCodeEncoder_Params()
        params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_L
        params.mode = cv2.QRCodeEncoder_MODE_BYTE
        random.seed(11)
        texts = [UPI, 'a', 'z' * 134] + [''.join(random.choice(string.printable[:94] + ' ') for _ in range(random.randint(1, 134)))
                                         for _ in range(150)]
        real = qr._masked
        for text in texts:
            version = qr._pick_version(len(text.encode()))
            params.version = version
            ref = cv2.QRCodeEncoder.create(params).encode(text)
            size = 17 + 4 * version
            edge = (ref.shape[0] - size) // 2
            expected = ref[edge:edge + size, edge:edge + size] == 0
            mod, func, spare = qr._layout(text)
            ignore = np.zeros((size, size), bool)
            for x, y in spare:
                ignore[y, x] = True
            best = None
            for mask in range(8):
                got = np.array(real(mod, func, mask))
                diff = int(((got != expected) & ~ignore).sum())
                best = diff if best is None else min(best, diff)
            self.assertEqual(best, 0, (len(text), version))
