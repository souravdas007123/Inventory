"""The salt (composition) is shown in its own coloured chip on the bill screen, and the lookup carries it for the chip."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Product
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class SaltChipTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_bill_screen_has_the_salt_chip_and_its_style(self):
        html = self.client.get('/bills/new/').content.decode()
        self.assertIn('saltChip(', html)
        self.assertIn('.badge.salt', html)
        self.assertIn('salt-txt', html)

    def test_lookup_sends_the_salt_for_the_chip(self):
        p = Product.objects.create(name='Saltytab 650', composition='Paracetamol 650mg', unit=self.tablet)
        data = self.client.get('/api/products/lookup/', {'q': 'Saltytab'}).json()
        self.assertEqual(data['results'][0]['composition'], 'Paracetamol 650mg')
