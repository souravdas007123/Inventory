"""Messages and questions use the popup in base.html, never the browser's own alert / confirm box."""
import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parents[2] / 'templates'
NATIVE = re.compile(r'(?<![A-Za-z_.])(?:window\.)?(?:alert|confirm|prompt)\s*\(')


class NoNativeBoxTests(SimpleTestCase):
    def test_no_template_calls_the_browsers_alert_confirm_or_prompt(self):
        found = []
        for path in TEMPLATES.rglob('*'):
            if path.suffix not in ('.html', '.js') or path.name == 'base.html':
                continue
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if NATIVE.search(line):
                    found.append(f"{path.relative_to(TEMPLATES)}:{n}")
        self.assertEqual(found, [], 'use appAlert() / appConfirm() or data-confirm instead')

    def test_the_popup_is_defined_once_in_the_base_template(self):
        base = (TEMPLATES / 'app' / 'base.html').read_text(encoding='utf-8')
        for word in ('id="app-dlg"', 'window.appAlert', 'window.appConfirm', 'data-confirm'):
            self.assertIn(word, base)
