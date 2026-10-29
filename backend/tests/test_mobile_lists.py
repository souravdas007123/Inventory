"""Small mobile-layout rules: no empty '-' lines in list cells, tidy cards on a phone."""
from django.test import SimpleTestCase

from backend.views import stack


class StackCellTests(SimpleTestCase):
    def test_a_lone_dash_is_dropped_when_there_is_more_to_show(self):
        html = str(stack('-', 'SKU-1', ''))
        self.assertNotIn('<div>-</div>', html)
        self.assertIn('SKU-1', html)

    def test_a_dash_stays_when_it_is_all_there_is(self):
        self.assertEqual(str(stack('-', '', '-')), '<div>-</div>')

    def test_a_real_first_line_is_kept(self):
        self.assertEqual(str(stack('Rack 2', 'x')), '<div>Rack 2</div><small class="sub">x</small>')


class PhoneCssTests(SimpleTestCase):
    def test_the_base_template_has_the_phone_card_rules(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parents[2] / 'templates' / 'app' / 'base.html').read_text()
        for rule in ('table.t.stack td.nil{display:none}', 'td.acts{display:flex', '.bars .col:nth-child(even) small'):
            self.assertIn(rule, css)
