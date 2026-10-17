"""The MedSync logo (capsule inside sync arrows) is used on every page and in the favicon and share image."""
import struct
import xml.etree.ElementTree as ET

from django.test import TestCase, override_settings

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'])
OLD_PLUS = 'M9 2h6v7h7v6h-7v7H9v-7H2V9h7z'


@override_settings(**SAFE)
class LogoTests(TestCase):
    def test_public_pages_use_the_new_logo(self):
        for path in ('/', '/login/', '/blog/'):
            html = self.client.get(path).content.decode()
            self.assertIn('viewBox="0 0 64 64"', html, path)
            self.assertNotIn(OLD_PLUS, html, path)
            self.assertNotIn('M13.6 10h4.8v3.6H22', html, path)         # the old plus inside the app logo

    def test_favicon_is_a_valid_svg(self):
        resp = self.client.get('/favicon.svg')
        self.assertEqual(resp.status_code, 200)
        root = ET.fromstring(b''.join(resp.streaming_content))
        self.assertTrue(root.tag.endswith('svg'))
        self.assertEqual(root.get('viewBox'), '0 0 64 64')

    def test_share_image_is_a_1200_by_630_png(self):
        resp = self.client.get('/og.png')
        data = b''.join(resp.streaming_content)
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', data[16:24]), (1200, 630))
