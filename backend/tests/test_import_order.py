"""Import / Export page: what a new shop needs first comes first, and every card says whether it is required."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend import views_import
from backend.imports import IMPORTERS

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


class OrderTests(TestCase):
    def test_suppliers_then_stock_then_products(self):
        keys = [c['key'] for c in views_import.import_cards()]
        self.assertEqual(keys[:3], ['suppliers', 'stock', 'products'])
        self.assertLess(keys.index('stock'), keys.index('products'))        # a stock entry creates the medicines, so Products can wait

    def test_every_importer_is_listed_exactly_once(self):
        keys = [c['key'] for c in views_import.import_cards()]
        self.assertCountEqual(keys, list(IMPORTERS))

    def test_only_the_first_two_are_required_and_they_have_a_step_and_a_reason(self):
        cards = views_import.import_cards()
        self.assertEqual([c['key'] for c in cards if c['required']], ['suppliers', 'stock'])
        self.assertEqual([c['step'] for c in cards[:3]], ['Step 1', 'Step 2', 'Step 3'])
        for c in cards:
            self.assertTrue(c['why'], c['key'])

    def test_an_importer_missing_from_the_guide_goes_last_and_is_optional(self):
        IMPORTERS['zzz_test'] = dict(IMPORTERS['units'], title='Zzz', icon='?', desc='x', export=None)
        try:
            cards = views_import.import_cards()
            self.assertEqual(cards[-1]['key'], 'zzz_test')
            self.assertFalse(cards[-1]['required'])
        finally:
            del IMPORTERS['zzz_test']


@override_settings(**SAFE)
class PageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-x'))

    def test_the_cards_appear_in_that_order_with_their_tags(self):
        html = self.client.get('/import/').content.decode()
        pos = [html.index(f'/import/{k}/') for k in ('suppliers', 'stock', 'products', 'customers')]
        self.assertEqual(pos, sorted(pos))
        self.assertIn('Step 1', html)
        self.assertEqual(html.count('>Required<'), 2)
        self.assertIn('Optional', html)
        self.assertIn('Why now:', html)

    def test_the_guide_text_is_in_english_and_says_where_to_start(self):
        html = self.client.get('/import/').content.decode()
        self.assertIn('Where to start', html)
        self.assertNotIn('chadhayein', html)
        self.assertNotIn(' se supplier', html)
