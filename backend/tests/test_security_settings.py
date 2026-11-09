"""Production switches: with DJANGO_HTTPS on, the cookies are secure, http goes to https and HSTS is on."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


def settings_with(**env):
    """The security settings the project ends up with under this environment (a fresh interpreter, like a real start)."""
    code = ("import os,sys,json; os.environ['DJANGO_SETTINGS_MODULE']='vaultyfly.settings'; "
            "from django.conf import settings as s; "
            "print(json.dumps(dict(ssl=s.SECURE_SSL_REDIRECT, sess=s.SESSION_COOKIE_SECURE, csrf=s.CSRF_COOKIE_SECURE, "
            "hsts=s.SECURE_HSTS_SECONDS, debug=s.DEBUG)))")
    base = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
    base.update({'DJANGO_SECRET_KEY': 'x' * 60, 'DJANGO_ALLOWED_HOSTS': 'example.com', 'PYTHONPATH': os.pathsep.join(sys.path)})
    base.update(env)
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=base, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-600:]
    import json
    return json.loads(done.stdout.strip().splitlines()[-1])


class ProductionSettingsTests(SimpleTestCase):
    def test_https_mode_is_locked_down(self):
        s = settings_with(DJANGO_HTTPS='1')
        self.assertEqual((s['ssl'], s['sess'], s['csrf'], s['debug']), (True, True, True, False))
        self.assertEqual(s['hsts'], 30 * 24 * 3600)

    def test_hsts_can_be_changed_and_a_typo_does_not_break_the_start(self):
        self.assertEqual(settings_with(DJANGO_HTTPS='1', DJANGO_HSTS_SECONDS='31536000')['hsts'], 31536000)
        self.assertEqual(settings_with(DJANGO_HTTPS='1', DJANGO_HSTS_SECONDS='0')['hsts'], 0)
        self.assertEqual(settings_with(DJANGO_HTTPS='1', DJANGO_HSTS_SECONDS='a year')['hsts'], 30 * 24 * 3600)
        self.assertEqual(settings_with(DJANGO_HTTPS='1', DJANGO_HSTS_SECONDS='-5')['hsts'], 0)

    def test_without_https_mode_nothing_is_forced(self):
        s = settings_with()
        self.assertEqual((s['ssl'], s['sess'], s['csrf'], s['hsts']), (False, False, False, 0))

    def test_no_secret_key_stops_a_production_start(self):
        code = "import os; os.environ['DJANGO_SETTINGS_MODULE']='vaultyfly.settings'; from django.conf import settings; settings.SECRET_KEY"
        base = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
        base['PYTHONPATH'] = os.pathsep.join(sys.path)
        done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=base, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', done.stderr)
