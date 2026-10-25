"""'Add to Home Screen' (manifest, service worker, icons) and the camera scan button."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class InstallableAppTests(TestCase):
    def test_the_manifest_is_public_and_valid(self):
        resp = self.client.get('/manifest.webmanifest')
        self.assertEqual((resp.status_code, resp['Content-Type']), (200, 'application/manifest+json'))
        data = json.loads(resp.content)
        self.assertEqual((data['display'], data['start_url'], data['short_name']), ('standalone', '/', 'MedSync'))
        sizes = {i['sizes'] for i in data['icons']}
        self.assertTrue({'192x192', '512x512'} <= sizes)
        self.assertIn('maskable', {i['purpose'] for i in data['icons']})

    def test_every_icon_in_the_manifest_is_served_as_a_png(self):
        for icon in json.loads(self.client.get('/manifest.webmanifest').content)['icons']:
            resp = self.client.get(icon['src'])
            self.assertEqual((resp.status_code, resp['Content-Type']), (200, 'image/png'), icon['src'])
            self.assertTrue(b''.join(resp.streaming_content).startswith(b'\x89PNG'))

    def test_the_service_worker_is_served_from_the_root_and_never_cached(self):
        resp = self.client.get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('javascript', resp['Content-Type'])
        self.assertEqual(resp['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', resp['Cache-Control'])
        body = resp.content.decode()
        self.assertNotIn('caches.', body)                 # nothing private is stored on the phone
        self.assertIn('No internet', body)

    def test_the_service_worker_is_valid_javascript(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'sw.js'
            f.write_text(self.client.get('/sw.js').content.decode(), encoding='utf-8')
            r = subprocess.run([node, '--check', str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_app_and_the_login_page_link_the_manifest(self):
        owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-xx')
        self.assertContains(self.client.get('/login/'), 'rel="manifest" href="/manifest.webmanifest"')
        self.client.force_login(owner)
        page = self.client.get('/help/')
        self.assertContains(page, 'rel="manifest" href="/manifest.webmanifest"')
        self.assertContains(page, 'name="theme-color"')
        self.assertContains(page, "serviceWorker.register('/sw.js'")

    def test_the_public_home_page_is_not_changed(self):
        self.assertNotContains(self.client.get('/'), 'serviceWorker')


@override_settings(**SAFE)
class CameraScanTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-xx')
        self.client.force_login(self.owner)

    def test_the_bill_and_purchase_screens_have_the_camera_scan_code(self):
        for url, field in (('/bills/new/', 'scan'), ('/purchases/new/', 'pq')):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, url)
            self.assertContains(page, 'cam-open', msg_prefix=url)
            self.assertContains(page, f"document.getElementById('{field}')", msg_prefix=url)
            self.assertContains(page, 'BarcodeDetector', msg_prefix=url)

    def test_other_screens_do_not_get_it(self):
        self.assertNotContains(self.client.get('/products/new/'), 'cam-open')

    def test_the_inline_scripts_are_valid_javascript(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        for url in ('/bills/new/', '/purchases/new/'):
            html = self.client.get(url).content.decode()
            for i, code in enumerate(re.findall(r'<script>(.*?)</script>', html, re.S)):
                with tempfile.TemporaryDirectory() as d:
                    f = Path(d) / 'x.js'
                    f.write_text(code, encoding='utf-8')
                    r = subprocess.run([node, '--check', str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f'{url} script {i}: {r.stderr[:400]}')
