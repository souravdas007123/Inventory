"""The public home page: visible without login, SEO files, and the dashboard still works for logged-in users."""
import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'])


@override_settings(**SAFE)
class LandingTests(TestCase):
    def test_visitors_see_the_home_page_without_logging_in(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<h1>')
        self.assertContains(resp, 'href="/login/"')

    def test_seo_tags_use_the_domain_the_visitor_came_on(self):
        html = self.client.get('/').content.decode()
        self.assertIn('<link rel="canonical" href="http://testserver/">', html)
        self.assertIn('content="http://testserver/og.png"', html)
        self.assertEqual(html.count('<h1>'), 1)
        self.assertIn('<meta name="description"', html)
        ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(len(json.loads(ld)['@graph']), 2)

    def test_title_and_description_are_short_enough_for_search_results(self):
        html = self.client.get('/').content.decode()
        desc = re.search(r'<meta name="description" content="([^"]*)">', html).group(1)
        title = re.search(r'<title>(.*?)</title>', html).group(1)
        self.assertLessEqual(len(desc), 155)            # about 1000 pixels: longer text is cut off in Google
        self.assertGreaterEqual(len(desc), 70)
        self.assertLessEqual(len(title), 62)            # about 600 pixels

    @override_settings(LANDING_WHATSAPP='+91 98765-43210')
    def test_whatsapp_number_makes_the_demo_buttons(self):
        html = self.client.get('/').content.decode()
        self.assertIn('https://wa.me/919876543210?text=', html)
        self.assertIn('Ask for a free demo', html)

    @override_settings(LANDING_WHATSAPP='')
    def test_without_a_number_the_buttons_lead_to_login(self):
        html = self.client.get('/').content.decode()
        self.assertNotIn('wa.me', html)
        self.assertIn('Login to MedSync', html)

    @override_settings(LANDING_WHATSAPP='')
    def test_the_top_menu_has_only_one_login(self):
        for url in ('/', '/blog/'):
            html = self.client.get(url).content.decode()
            nav = re.search(r'<nav aria-label="Main">(.*?)</nav>', html, re.S).group(1)
            self.assertEqual(nav.count('>Login<'), 1, url)

    @override_settings(LANDING_WHATSAPP='+91 98765-43210')
    def test_with_a_demo_button_the_menu_still_has_a_login_link(self):
        nav = re.search(r'<nav aria-label="Main">(.*?)</nav>', self.client.get('/').content.decode(), re.S).group(1)
        self.assertEqual(nav.count('>Login<'), 1)
        self.assertIn('Get a demo', nav)

    @override_settings(LANDING_WHATSAPP='abc')
    def test_a_bad_number_is_ignored(self):
        self.assertNotIn('wa.me', self.client.get('/').content.decode())

    def test_robots_and_sitemap(self):
        robots = self.client.get('/robots.txt')
        self.assertEqual(robots['Content-Type'], 'text/plain')
        self.assertContains(robots, 'Sitemap: http://testserver/sitemap.xml')
        self.assertContains(robots, 'Disallow: /')
        sitemap = self.client.get('/sitemap.xml')
        self.assertEqual(sitemap['Content-Type'], 'application/xml')
        self.assertContains(sitemap, '<loc>http://testserver/</loc>')

    def test_share_image_and_icon_are_served(self):
        png = self.client.get('/og.png')
        self.assertEqual((png.status_code, png['Content-Type']), (200, 'image/png'))
        self.assertIn('max-age=86400', png['Cache-Control'])
        self.assertEqual(self.client.get('/favicon.svg')['Content-Type'], 'image/svg+xml')

    def test_the_app_pages_still_need_a_login(self):
        resp = self.client.get('/products/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_logged_in_users_still_get_the_dashboard(self):
        user = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(user)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'See what it does')


@override_settings(**SAFE)
class LogoutAndLoginLinksTests(TestCase):
    def test_logging_out_leads_to_the_home_page_which_has_a_login_link(self):
        user = get_user_model().objects.create_user('clerk', password='pw-12345')
        self.client.force_login(user)
        resp = self.client.post('/logout/')
        self.assertEqual((resp.status_code, resp['Location']), (302, '/'))
        self.assertContains(self.client.get('/'), 'href="/login/"')

    def test_the_login_page_links_back_to_the_home_page(self):
        resp = self.client.get('/login/')
        self.assertContains(resp, 'href="/">')
        self.assertContains(resp, 'Back to home page')
