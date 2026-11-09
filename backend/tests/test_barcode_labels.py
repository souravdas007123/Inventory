"""EAN-13 barcode labels: the encoder, the label page and the shop-code maker."""
import random
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, SimpleTestCase, TestCase, override_settings

from backend import barcode
from backend.models import Product, ShopProfile
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


class EncoderTests(SimpleTestCase):
    def test_check_digit_known_codes(self):
        self.assertEqual(barcode.check_digit('400638133393'), '1')            # 4006381333931
        self.assertEqual(barcode.check_digit('590123412345'), '7')            # 5901234123457
        self.assertTrue(barcode.is_valid('4006381333931'))
        self.assertFalse(barcode.is_valid('4006381333932'))
        for bad in ('', None, '123', 'abcdefghijklm', '40063813339311', '4006381333 31'):
            self.assertFalse(barcode.is_valid(bad), bad)

    def test_shop_code_is_valid_unique_and_in_store_range(self):
        codes = [barcode.shop_code(n) for n in (1, 2, 3, 99, 12345, 99999999999)]
        self.assertEqual(len(set(codes)), len(codes))
        for c in codes:
            self.assertTrue(barcode.is_valid(c))
            self.assertEqual(len(c), 13)
            self.assertEqual(c[0], '2')                                      # 2xx = reserved for in-store use
        self.assertEqual(barcode.shop_code(1), '2000000000015')
        with self.assertRaises(ValueError):
            barcode.shop_code(10 ** 11)

    def test_modules_have_the_ean_structure(self):
        for c in ('4006381333931', barcode.shop_code(7), '5901234123457'):
            m = barcode.modules(c)
            self.assertEqual(len(m), 95)
            self.assertEqual((m[:3], m[45:50], m[-3:]), ('101', '01010', '101'))
        with self.assertRaises(ValueError):
            barcode.modules('4006381333930')

    def test_kinds(self):
        self.assertEqual(barcode.kind('4006381333931'), 'ean13')
        self.assertEqual(barcode.kind(' 036000291452 '), 'upca')            # 12 digits (UPC-A)
        self.assertEqual(barcode.kind('96385074'), 'ean8')
        for bad in ('', None, '890200001031', '8901003170', '96385075', '4006381333930', 'ABC-123', '12345'):
            self.assertEqual(barcode.kind(bad), '', bad)
        self.assertEqual(barcode.check_digit8('9638507'), '4')

    def test_svg_for_upca_and_ean8(self):
        self.assertTrue(barcode.svg('036000291452').startswith('<svg'))
        eight = barcode.svg('96385074')
        self.assertTrue(eight.startswith('<svg') and '9638' in eight and '5074' in eight)
        self.assertEqual(barcode.modules8('96385074')[:3] + barcode.modules8('96385074')[-3:], '101101')
        self.assertEqual(len(barcode.modules8('96385074')), 67)

    def test_svg(self):
        art = barcode.svg('4006381333931')
        self.assertTrue(art.startswith('<svg') and art.endswith('</svg>'))
        self.assertIn('4006381333931'[0], art)
        self.assertEqual(barcode.svg('nonsense'), '')
        self.assertEqual(barcode.svg('4006381333930'), '')
        self.assertNotIn('<text', barcode.svg('4006381333931', text=False))


try:
    import cv2
    import numpy as np
    HAVE_CV = hasattr(cv2, 'barcode')
except Exception:                                  # OpenCV is only a test helper: the check is skipped without it
    HAVE_CV = False


@unittest.skipUnless(HAVE_CV, 'OpenCV with the barcode module is not installed')
class IndependentDecoderTests(SimpleTestCase):
    """The bars are drawn from the encoder and read back by OpenCV, a decoder that shares no code with it."""

    @staticmethod
    def read(code):
        m = barcode.modules8(code) if barcode.kind(code) == 'ean8' else barcode.modules(('0' + code) if barcode.kind(code) == 'upca' else code)
        det = cv2.barcode.BarcodeDetector()
        for scale, h, blur in ((2, 80, 0), (2, 80, 1), (3, 160, 1), (4, 160, 1), (3, 100, 0)):
            w = len(m) * scale
            im = np.full((h + 120, w + 120), 255, np.uint8)
            for i, c in enumerate(m):
                if c == '1':
                    im[60:60 + h, 60 + i * scale:60 + (i + 1) * scale] = 0
            if blur:
                im = cv2.GaussianBlur(im, (3, 3), 0)
            r = det.detectAndDecodeWithType(im)
            if r[0] and r[1] and r[1][0]:
                return r[1][0]
        return ''

    def test_many_codes_read_back_exactly(self):
        rnd = random.Random(5)
        codes = [barcode.shop_code(n) for n in (1, 2, 7, 99, 12345, 99999999999)] + ['4006381333931', '5901234123457']
        codes += [barcode.shop_code(rnd.randint(1, 10 ** 9)) for _ in range(30)]
        bad = [c for c in codes if self.read(c) != c]
        self.assertEqual(bad, [])

    def test_ean8_and_upca_read_back(self):
        rnd = random.Random(8)
        eights = [(x := f'{rnd.randint(1000000, 9999999)}') + barcode.check_digit8(x) for _ in range(15)] + ['96385074']
        upcs = ['036000291452']
        for c in eights + upcs:
            self.assertEqual(self.read(c), c, c)


@override_settings(**SAFE)
class LabelPageTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.c.force_login(self.owner)
        self.have = Product.objects.create(name='Has Code', unit=self.tablet, mrp=Decimal('12.50'), barcode='4006381333931')
        self.none = Product.objects.create(name='No Code', unit=self.tablet, mrp=Decimal('3.00'))
        self.odd = Product.objects.create(name='Odd Code', unit=self.tablet, barcode='ABC-123')

    def page(self, qs):
        return self.c.get('/labels/' + qs)

    def test_valid_code_gives_labels_and_copies(self):
        r = self.page(f'?ids={self.have.pk}&n{self.have.pk}=3')
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(html.count('class="lab"'), 3)
        self.assertIn('Has Code', html)
        self.assertIn('4006381333931', html)
        self.assertIn('Print 3 labels', html)

    def test_product_without_code_and_odd_code_are_explained_not_drawn(self):
        html = self.page(f'?ids={self.none.pk},{self.odd.pk}').content.decode()
        self.assertEqual(html.count('class="lab"'), 0)
        self.assertIn('No barcode', html)
        self.assertIn('can be drawn here', html)
        self.assertIn('Create a shop barcode', html)

    def test_junk_in_the_address_is_safe(self):
        for qs in ('', '?ids=', '?ids=abc,,-1,9999999', f'?ids={self.have.pk}&n{self.have.pk}=zzz&size=bogus', '?purchase=x', '?purchase=999999', f'?ids={self.have.pk}&n{self.have.pk}=99999'):
            self.assertEqual(self.page(qs).status_code, 200, qs)
        self.assertLessEqual(self.page(f'?ids={self.have.pk}&n{self.have.pk}=99999').content.decode().count('class="lab"'), 200)

    def test_sizes_change_the_label_box(self):
        self.assertIn('width:70mm', self.page(f'?ids={self.have.pk}&size=a4x24').content.decode())
        self.assertIn('width:52.5mm', self.page(f'?ids={self.have.pk}&size=a4x40').content.decode())
        roll = self.page(f'?ids={self.have.pk}&size=roll').content.decode()
        self.assertIn('width:50mm', roll)
        self.assertIn('size:50mm 25mm', roll)

    def test_purchase_labels_use_the_quantity_bought(self):
        self.buy(qty=4, invoice='LAB-1', product=self.have, unit='tablet', price='1.00')
        from backend.models import Purchase
        pur = Purchase.objects.get(invoice_number='LAB-1')
        self.assertEqual(self.page(f'?purchase={pur.pk}').content.decode().count('class="lab"'), 4)

    def test_products_and_purchases_lists_link_to_the_labels(self):
        self.assertIn(f'/labels/?ids={self.have.pk}', self.c.get('/products/').content.decode())


@override_settings(**SAFE)
class MakeCodeTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))
        self.a = Product.objects.create(name='A', unit=self.tablet)
        self.b = Product.objects.create(name='B', unit=self.tablet)
        self.has = Product.objects.create(name='Has', unit=self.tablet, barcode='8901234567890')

    def make(self, ids, **kw):
        return self.c.post('/labels/codes/', dict(ids=','.join(str(i) for i in ids), **kw))

    def test_codes_are_created_only_where_there_is_none(self):
        r = self.make([self.a.pk, self.b.pk, self.has.pk])
        self.assertEqual(r.status_code, 302)
        for p in (self.a, self.b):
            p.refresh_from_db()
            self.assertTrue(barcode.is_valid(p.barcode))
            self.assertTrue(p.barcode.startswith('2'))
        self.assertNotEqual(self.a.barcode, self.b.barcode)
        self.has.refresh_from_db()
        self.assertEqual(self.has.barcode, '8901234567890')           # an existing barcode is never touched

    def test_running_it_twice_changes_nothing(self):
        self.make([self.a.pk])
        self.a.refresh_from_db()
        first = self.a.barcode
        self.make([self.a.pk])
        self.a.refresh_from_db()
        self.assertEqual(self.a.barcode, first)

    def test_a_taken_code_is_skipped_not_reused(self):
        Product.objects.filter(pk=self.has.pk).update(barcode=barcode.shop_code(self.a.pk))     # the code a would get is taken
        self.make([self.a.pk])
        self.a.refresh_from_db()
        self.assertTrue(barcode.is_valid(self.a.barcode))
        self.assertNotEqual(self.a.barcode, barcode.shop_code(self.a.pk))

    def test_get_is_refused_and_next_cannot_leave_the_site(self):
        self.assertEqual(self.c.get('/labels/codes/').status_code, 405)
        r = self.make([self.a.pk], next='https://evil.example/')
        self.assertEqual(r['Location'], '/labels/')
        r = self.make([self.b.pk], next='//evil.example/labels/')
        self.assertEqual(r['Location'], '/labels/')

    def test_permissions(self):
        clerk = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        c = Client()
        c.force_login(clerk)
        self.assertEqual(c.get('/labels/').status_code, 403)
        self.assertEqual(c.post('/labels/codes/', {'ids': str(self.a.pk)}).status_code, 403)
        clerk.user_permissions.add(Permission.objects.get(codename='view_product'))
        clerk = get_user_model().objects.get(pk=clerk.pk)
        c.force_login(clerk)
        self.assertEqual(c.get(f'/labels/?ids={self.a.pk}').status_code, 200)
        self.assertEqual(c.post('/labels/codes/', {'ids': str(self.a.pk)}).status_code, 403)       # view only: cannot create codes
        self.a.refresh_from_db()
        self.assertIsNone(self.a.barcode)


@override_settings(**SAFE)
class LabelPageDarkModeTests(StockMixin, TestCase):
    def test_highlighted_row_does_not_use_a_fixed_light_colour(self):
        """A light fixed background made the (light) text unreadable in dark mode: the highlight must be see-through."""
        c = Client()
        c.force_login(get_user_model().objects.create_superuser('dm', 'dm@x.com', 'pw'))
        p = Product.objects.create(name='No Code Dark', unit=self.tablet)
        html = c.get(f'/labels/?ids={p.pk}').content.decode()
        self.assertIn('.lab-warn td{background:rgba(', html)
        self.assertNotIn('#fff7e6', html)
