"""The tutorial video: the file is a real video, it is served (with ranges, so phones can seek), and it is on the Help and Setup pages."""
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
VIDEO, POSTER = 'video/medsync-getting-started.mp4', 'video/medsync-getting-started.jpg'


@override_settings(**SAFE)
class TutorialVideoTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_the_files_are_real_and_not_huge(self):
        mp4, jpg = finders.find(VIDEO), finders.find(POSTER)
        self.assertTrue(mp4 and jpg)
        with open(mp4, 'rb') as f:
            head = f.read(12)
        self.assertEqual(head[4:8], b'ftyp')                      # an MP4 file
        import os
        self.assertTrue(100_000 < os.path.getsize(mp4) < 6_000_000, 'small enough for WhatsApp and slow connections')
        with open(jpg, 'rb') as f:
            self.assertEqual(f.read(3), b'\xff\xd8\xff')

    def test_help_and_setup_pages_have_the_player(self):
        for url in ('/help/', '/setup/'):
            html = self.client.get(url).content.decode()
            self.assertEqual(len(re.findall(r'<video ', html)), 1, url)
            self.assertIn(f'/{settings.STATIC_URL.strip("/")}/{VIDEO}', html, url)
            self.assertIn('controls', html)
            self.assertIn('poster=', html)

    def test_the_dashboard_card_links_to_the_video(self):
        html = self.client.get('/').content.decode()
        self.assertIn('/help/#video', html)
        self.assertIn('id="video"', self.client.get('/help/').content.decode())

    def test_the_video_is_served_with_ranges(self):
        from whitenoise.responders import StaticFile  # noqa: F401  (WhiteNoise is what serves static files in production)
        from django.test import RequestFactory
        import tempfile, shutil, os
        from whitenoise import WhiteNoise
        path = finders.find(VIDEO)
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, 'video'))
            shutil.copy(path, os.path.join(root, VIDEO))
            app = WhiteNoise(lambda e, s: None, root=root, prefix='static/')
            out = {}

            def start(status, headers):
                out['status'], out['headers'] = status, dict(headers)
            environ = RequestFactory().get('/static/' + VIDEO, HTTP_RANGE='bytes=0-99').environ
            environ['wsgi.input'] = None
            body = b''.join(app(environ, start))
            self.assertTrue(out['status'].startswith('206'), out['status'])
            self.assertEqual(len(body), 100)
            self.assertIn('video/mp4', out['headers']['Content-Type'])
        finally:
            shutil.rmtree(root, ignore_errors=True)
