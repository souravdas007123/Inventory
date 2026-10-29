"""Footer social media links: edited in the admin, shown as icons, safe, hidden when there are none."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings

from backend import sitecontent
from backend.models import SocialLink
from backend.social_icons import ICONS

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


def home():
    return Client().get('/').content.decode()


@override_settings(**SAFE)
class SocialLinkPageTests(TestCase):
    def test_no_links_means_no_follow_row_and_the_share_links_stay(self):
        page = home()
        self.assertNotIn('Follow us', page)
        self.assertIn('Share MedSync:', page)

    def test_an_active_link_shows_as_an_icon_with_a_label(self):
        SocialLink.objects.create(platform='instagram', url='https://www.instagram.com/medsync')
        page = home()
        self.assertIn('Follow us', page)
        self.assertIn('href="https://www.instagram.com/medsync"', page)
        self.assertIn('aria-label="Instagram"', page)
        self.assertIn('<svg viewBox="0 0 24 24"', page)
        self.assertIn('rel="noopener nofollow me"', page)

    def test_order_and_hidden_links(self):
        SocialLink.objects.create(platform='youtube', url='https://youtube.com/@medsync', position=20)
        SocialLink.objects.create(platform='facebook', url='https://facebook.com/medsync', position=10)
        SocialLink.objects.create(platform='x', url='https://x.com/medsync', position=5, active=False)
        page = home()
        self.assertLess(page.index('facebook.com/medsync'), page.index('youtube.com/@medsync'))
        self.assertNotIn('x.com/medsync', page)

    def test_every_platform_has_an_icon(self):
        for key, _ in SocialLink.PLATFORMS:
            self.assertIn(key, ICONS)
            SocialLink.objects.create(platform=key, url=f'https://example.com/{key}')
        self.assertEqual(len(sitecontent.social_links()), len(SocialLink.PLATFORMS))
        self.assertEqual(home().count('aria-label='), len(SocialLink.PLATFORMS) + page_labels_other(home()))

    def test_a_quote_in_a_link_cannot_break_the_page(self):
        SocialLink.objects.filter().delete()
        SocialLink.objects.create(platform='website', url='https://a.example/x?q=1&y="><script>alert(1)</script>')
        page = home()
        self.assertNotIn('<script>alert(1)', page)

    def test_a_broken_table_does_not_break_the_page(self):
        from unittest import mock
        with mock.patch('backend.models.SocialLink.objects') as m:
            m.filter.side_effect = RuntimeError('db down')
            self.assertEqual(sitecontent.social_links(), [])
        self.assertEqual(Client().get('/').status_code, 200)


def page_labels_other(page):
    """aria-labels on the page that are not social icons."""
    return page.count('aria-label=') - page.count('rel="noopener nofollow me"')


class SocialLinkValidationTests(TestCase):
    def check(self, url):
        SocialLink(platform='facebook', url=url).full_clean()

    def test_only_full_https_links_are_accepted(self):
        self.check('https://facebook.com/medsync')
        for bad in ('', 'facebook.com/medsync', 'http://facebook.com/x', 'javascript:alert(1)', 'https://a b.com/x', 'https://'):
            with self.assertRaises(ValidationError, msg=bad):
                self.check(bad)

    def test_the_url_is_trimmed(self):
        link = SocialLink(platform='facebook', url='  https://facebook.com/medsync  ')
        link.full_clean()
        self.assertEqual(link.url, 'https://facebook.com/medsync')


@override_settings(**SAFE)
class SocialLinkAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345'))

    def test_admin_pages_open_and_are_in_the_website_section(self):
        self.assertIn('Social Media Links', self.client.get('/admin/').content.decode())
        for url in ('/admin/backend/sociallink/', '/admin/backend/sociallink/add/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_add_edit_and_a_bad_link_in_the_admin(self):
        r = self.client.post('/admin/backend/sociallink/add/', dict(platform='linkedin', url='https://linkedin.com/company/medsync', position=1, active='on'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('linkedin.com/company/medsync', home())
        bad = self.client.post('/admin/backend/sociallink/add/', dict(platform='facebook', url='facebook.com/x', position=2, active='on'))
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, 'starting with https://')
        self.assertEqual(SocialLink.objects.count(), 1)
