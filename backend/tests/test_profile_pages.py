"""`manage.py profile_pages` only measures pages (GET) and must never crash on a missing or broken page."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class ProfilePagesTests(TestCase):
    def run_cmd(self, *paths):
        out = StringIO()
        call_command('profile_pages', *paths, stdout=out)
        return out.getvalue()

    def test_it_needs_a_superuser(self):
        with self.assertRaises(CommandError):
            self.run_cmd()

    def test_it_prints_queries_and_time_for_a_page(self):
        get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345')
        text = self.run_cmd('/', '/no-such-page/')
        lines = text.splitlines()
        self.assertIn('queries', lines[0])
        self.assertTrue(any(line.startswith('/ ') for line in lines), text)
        self.assertFalse(any(line.startswith('/no-such-page/') for line in lines))   # a 404 is skipped
