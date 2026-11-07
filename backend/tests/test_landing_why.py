"""The "Built for how medical stores really work" section of the public home page: only claims that the application supports."""
import re

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import resolve

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class WhySectionTests(TestCase):
    def setUp(self):
        r = Client().get('/')
        self.assertEqual(r.status_code, 200)
        self.page = r.content.decode()

    def test_the_six_cards_are_on_the_page(self):
        sec = re.search(r'<section id="why">(.*?)</section>', self.page, re.S).group(1)
        titles = re.findall(r'<span class="w7 sm">(.*?)</span>', sec)
        self.assertEqual(titles, ['Keep billing when the internet drops', 'GSTR-2B check', 'The owner hears about risky changes',
                                  'Reminders that are ready to use', 'The salt is always in sight', 'Scan with a phone camera'])

    def test_extra_items_were_added_to_the_rest_of_the_shop_list(self):
        for name in ('Fast and dead stock', 'Medicine Finder', 'Live search', '2-step sign-in'):
            self.assertIn(f'<span class="w7 sm">{name}</span>', self.page)

    def test_no_claim_that_competitors_lack_something(self):
        low = self.page.lower()
        for phrase in ('no other software', 'only symaso', 'unlike others', 'nobody else offers', 'no competitor', 'no one else'):
            self.assertNotIn(phrase, low)

    def test_the_features_named_exist_in_the_application(self):
        for url in ('/finder/', '/gstr2b/', '/reminders/', '/refill/', '/movement/', '/security/', '/audit/'):
            self.assertTrue(resolve(url))
        for name in ('backend/static/js/offline.js', 'backend/staff_alerts.py', 'backend/gstr2b.py', 'backend/pwa.py'):
            import os
            self.assertTrue(os.path.exists(name), name)
