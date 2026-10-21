"""Things that work on SQLite but fail on PostgreSQL, caught by reading the code (no PostgreSQL needed)."""
import re
from pathlib import Path

from django.test import SimpleTestCase

BACKEND = Path(__file__).resolve().parent.parent


class PostgresReadyTests(SimpleTestCase):
    def test_row_locks_with_select_related_lock_only_their_own_row(self):
        """SQLite ignores select_for_update(); PostgreSQL refuses it together with select_related() on an optional link
        ("FOR UPDATE cannot be applied to the nullable side of an outer join") unless of=('self',) is given."""
        bad = []
        for f in BACKEND.glob('*.py'):
            for n, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r'select_for_update\(\)[^\n]*select_related|select_related[^\n]*select_for_update\(\)', line):
                    bad.append(f'{f.name}:{n}')
        self.assertEqual(bad, [])

    def test_the_settings_do_not_contain_a_password(self):
        text = (BACKEND.parent / 'vaultyfly' / 'settings.py').read_text(encoding='utf-8')
        for m in re.finditer(r"'PASSWORD'\s*:\s*([^\n]+)", text):
            self.assertIn('os.environ', m.group(1), m.group(0))
