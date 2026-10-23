"""The server and a new computer install everything with `pip install -r requirements.txt`, so the PostgreSQL driver
and the web server must be listed there."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class RequirementsTests(SimpleTestCase):
    def test_postgresql_driver_and_gunicorn_are_listed(self):
        text = (Path(settings.BASE_DIR) / 'requirements.txt').read_text().lower()
        self.assertIn('psycopg2-binary', text)
        self.assertIn('gunicorn', text)
        self.assertIn('django-tenants', text)           # needed when MULTI_SHOP=1
