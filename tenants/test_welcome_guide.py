"""The welcome e-mail is also a short user guide: first-day steps, quick links, and nothing unsafe in it."""
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from tenants import mails


def shop(name='Sharma <b>Medical</b>', owner='Raj <i>S</i>'):
    return SimpleNamespace(name=name, owner_name=owner, schema_name='s_x', trial_ends=date(2026, 12, 1))


def welcome(address='http://x.localhost/', **kw):
    with mock.patch('tenants.mails.shop_address', return_value=address), mock.patch('tenants.mails._contact_lines', return_value=['Email: help@example.com']):
        return mails.welcome_message(shop(**kw), 'raj@example.com')


class GuideTests(SimpleTestCase):
    def test_the_first_day_steps_are_in_the_right_order(self):
        _, text, html = welcome()
        order = ['shop details', 'suppliers', 'opening stock', 'first bill', '2-step']
        positions = [text.index(w) for w in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('Your first day, step by step', text)
        self.assertIn('Stock Entry', text)
        self.assertIn('expiry date', text)

    def test_quick_links_are_in_both_versions(self):
        _, text, html = welcome()
        for path in ('setup/', 'help/', 'import/', 'security/', 'support/'):
            self.assertIn(f'http://x.localhost/{path}', text, path)
            self.assertIn(f'href="http://x.localhost/{path}"', html, path)
        self.assertIn('http://x.localhost/login/', html)

    def test_without_a_shop_address_there_are_no_links_at_all(self):
        _, text, html = welcome(address='')
        self.assertNotIn('Quick links', text)
        self.assertNotIn('href=', html)
        self.assertNotIn('None', text)

    def test_what_people_typed_is_escaped_in_the_html(self):
        _, text, html = welcome()
        self.assertNotIn('<b>Medical</b>', html)
        self.assertNotIn('<i>S</i>', html)
        self.assertIn('&lt;b&gt;Medical&lt;/b&gt;', html)
        self.assertIn('Sharma <b>Medical</b>', text)

    def test_a_password_is_never_mentioned_as_a_value(self):
        for via_google in (False, True):
            with mock.patch('tenants.mails.shop_address', return_value='http://x.localhost/'), mock.patch('tenants.mails._contact_lines', return_value=[]):
                _, text, html = mails.welcome_message(shop(), 'raj@example.com', via_google=via_google)
            self.assertIn('never e-mail passwords', text)
            self.assertNotIn('Your password is:', text)

    def test_a_link_label_or_url_with_markup_cannot_break_the_html(self):
        html = mails._html('p', 't', 'i', [('links', [('<script>x</script>', 'http://a/"><script>y</script>')])])
        self.assertNotIn('<script>', html)

    def test_the_guide_is_plain_english(self):
        _, text, html = welcome()
        for hinglish in ('chadhayein', ' kare', 'karein', 'aap '):
            self.assertNotIn(hinglish, text)
