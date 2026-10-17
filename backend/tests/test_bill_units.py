"""Billing screen: the product lookup tells the screen which units a medicine can be sold in (base unit first), so the
row can show only those and start with the base unit."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import Product, ProductUnit, Unit

from .helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


@override_settings(**SAFE)
class BillUnitTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xyz'))

    def lookup(self, **params):
        return self.client.get('/api/products/lookup/', params).json()['results']

    def test_base_unit_comes_first_then_the_packs_smallest_first(self):
        box = Unit.objects.create(name='Box')
        ProductUnit.objects.create(product=self.product, unit=box, factor=100)
        [r] = self.lookup(id=self.product.pk)
        self.assertEqual(r['unit_id'], self.tablet.pk)
        self.assertEqual(r['units'], [dict(id=self.tablet.pk, name='Tablet', factor=1),
                                      dict(id=self.strip.pk, name='Strip', factor=10),
                                      dict(id=box.pk, name='Box', factor=100)])
        self.assertEqual(r['factors'][str(self.strip.pk)], 10)          # the old field is still there

    def test_a_medicine_with_only_a_base_unit(self):
        syrup = Product.objects.create(name='Cough Syrup', unit=Unit.objects.create(name='Bottle'), gst_rate=Decimal('18.00'))
        [r] = self.lookup(id=syrup.pk)
        self.assertEqual(r['units'], [dict(id=syrup.unit_id, name='Bottle', factor=1)])

    def test_a_medicine_without_a_base_unit_gives_an_empty_list_and_works(self):
        Product.objects.create(name='No Unit Item', gst_rate=Decimal('5.00'))
        [r] = self.lookup(q='No Unit Item')
        self.assertEqual(r['units'], [])
        self.assertIsNone(r['unit_id'])

    def test_search_results_carry_the_units_too(self):
        [r] = self.lookup(q='Dolo')
        self.assertEqual([u['name'] for u in r['units']], ['Tablet', 'Strip'])

    def test_the_bill_screen_has_the_unit_helpers(self):
        html = self.client.get(reverse('new', kwargs={'key': 'bills'})).content.decode()
        for marker in ('function ensureUnits', 'function drawUnits', 'function drawPills', 'RESUMING'):
            self.assertIn(marker, html)
