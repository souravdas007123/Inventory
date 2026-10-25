import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase


class ForgotPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('owner1', 'owner1@example.com', 'OldPass#2468')

    def ask(self, email='owner1@example.com'):
        return self.client.post('/forgot/', {'email': email})

    def link(self):
        return re.search(r'https?://[^/\s]+(/reset-password/\S+)', mail.outbox[-1].body).group(1)

    def test_login_page_links_to_forgot(self):
        self.assertContains(self.client.get('/login/'), 'href="/forgot/"')

    def test_all_sign_in_pages_share_the_new_layout(self):
        for url in ('/login/', '/forgot/'):
            page = self.client.get(url).content.decode()
            self.assertIn('class="shell"', page, url)
            self.assertIn('MedSync', page, url)
        self.assertContains(self.client.get('/login/'), 'Show</button>')       # the show-password button

    def test_pages_open(self):
        for url in ('/forgot/', '/forgot/sent/', '/reset-password/done/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_known_and_unknown_address_look_the_same(self):
        a = self.ask()
        b = self.ask('nobody@example.com')
        self.assertEqual((a.status_code, a['Location']), (b.status_code, b['Location']))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/reset-password/', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['owner1@example.com'])

    def test_full_flow_and_link_works_once(self):
        self.ask()
        path = self.link()
        r = self.client.get(path, follow=True)
        self.assertContains(r, 'Choose a new password')
        r = self.client.post(r.redirect_chain[-1][0], {'new_password1': 'BrandNew#97531', 'new_password2': 'BrandNew#97531'})
        self.assertRedirects(r, '/reset-password/done/')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNew#97531'))
        self.assertContains(self.client.get(path, follow=True), 'not valid any more')

    def test_weak_or_different_password_is_refused(self):
        self.ask()
        r = self.client.get(self.link(), follow=True)
        url = r.redirect_chain[-1][0]
        self.client.post(url, {'new_password1': '12345678', 'new_password2': '12345678'})
        self.client.post(url, {'new_password1': 'BrandNew#97531', 'new_password2': 'Different#1234'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass#2468'))

    def test_bad_link(self):
        self.assertContains(self.client.get('/reset-password/abc/set-password/', follow=True), 'not valid any more')

    def test_one_address_gets_only_three_mails_an_hour(self):
        for _ in range(6):
            self.ask()
        self.assertEqual(len(mail.outbox), 3)

    def test_one_visitor_is_limited(self):
        for i in range(15):
            self.ask('x%d@example.com' % i)
        get_user_model().objects.create_user('o2', 'o2@example.com', 'OldPass#2468')
        self.ask('o2@example.com')
        self.assertEqual(len(mail.outbox), 0)

    def test_bad_email_shows_message(self):
        self.assertContains(self.client.post('/forgot/', {'email': 'nope'}), 'valid email')
