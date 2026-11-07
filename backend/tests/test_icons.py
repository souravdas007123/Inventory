"""The screen uses one set of line icons, not emoji. Every icon a template, the menu or a page script asks for must exist."""
import glob
import re

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase, override_settings

from backend import views
from backend.templatetags import icons

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
EMOJI = re.compile('[\U0001F300-\U0001FAFF✅❌⚠⬆⬇⏸⏳⚖♻✉✏✔]')


class IconSetTests(TestCase):
    def test_every_icon_named_in_a_template_exists(self):
        names = set()
        for f in glob.glob('templates/**/*.html', recursive=True) + glob.glob('templates/**/*.js', recursive=True):
            t = open(f, encoding='utf8').read()
            names |= set(re.findall(r"\{%\s*icon\s+'([a-z-]+)'", t))
            names |= set(re.findall(r"'@([a-z-]+)[ ']", t))
        self.assertTrue(names)
        missing = {n for n in names if n not in icons.ICONS and n not in icons.ALIASES}
        self.assertEqual(missing, set())

    def test_every_menu_entry_has_a_real_icon(self):
        for n in views.NAV:
            self.assertIn(n['icon'], icons.ICONS, n['key'])

    def test_every_import_and_activity_icon_exists(self):
        from backend import imports
        for imp in imports.IMPORTERS.values():
            self.assertIn(imp['icon'], icons.ICONS, imp['title'])
        for name in ('receipt', 'undo', 'cash'):
            self.assertIn(name, icons.ICONS)

    def test_icons_the_page_scripts_use_exist(self):
        for n in icons.JS_ICONS:
            self.assertIn(n, icons.ICONS, n)

    def test_the_tag_draws_an_svg_in_the_text_colour(self):
        html = Template("{% load icons %}{% icon 'check' %}").render(Context())
        self.assertIn('<svg class="ic"', html)
        self.assertIn('<path', html)
        self.assertEqual(Template("{% load icons %}{% icon 'no-such-icon' %}").render(Context()), '')

    def test_emoji_in_python_text_become_icons_and_the_rest_is_escaped(self):
        out = Template("{% load icons %}{{ t|emoji_icons }}").render(Context({'t': '🖨 Print <b>'}))
        self.assertIn('<svg', out)
        self.assertNotIn('🖨', out)
        self.assertIn('Print &lt;b&gt;', out)


@override_settings(**SAFE)
class NoEmojiOnScreenTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_main_pages_show_no_emoji(self):
        for url in ('/', '/bills/', '/bills/new/', '/products/', '/setup/', '/import/', '/health/', '/reminders/', '/expiry/', '/suppliers/',
                    '/purchase_orders/', '/reorder/', '/batches/', '/customers/'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIsNone(EMOJI.search(r.content.decode()), url)

    def test_a_message_gets_its_status_icon(self):
        r = self.client.post('/categories/new/', {'name': 'Syrups'}, follow=True)
        html = r.content.decode()
        self.assertIn('msg msg-row success', html)
        self.assertIn('msg-ic', html)
        self.assertIsNone(EMOJI.search(html))


class ScreenLayoutRulesTests(TestCase):
    """Rules found on real screens: an info banner with bold text or a link inside must not be a flex row (its words fell apart),
    a tick box in a table must not be as wide as a text box, and the calendar button must be visible on the dark theme."""

    def css(self):
        return open('templates/app/base.html', encoding='utf8').read()

    def test_only_flash_messages_are_flex_rows(self):
        css = self.css()
        self.assertNotIn('.msg{display:flex', css)
        self.assertIn('.msg-row{display:flex', css)
        self.assertIn('class="msg msg-row {{ m.tags }}"', css)

    def test_tick_boxes_in_tables_are_not_text_box_wide(self):
        self.assertIn('.t input[type=checkbox],.t input[type=radio]{min-width:0}', self.css())

    def test_the_calendar_button_shows_on_the_dark_theme(self):
        self.assertIn('input::-webkit-calendar-picker-indicator{filter:invert(', self.css())
