"""Many shops in one install. The switch is OFF unless MULTI_SHOP=1 in .env, so most of these tests only check that
nothing changed; the schema test runs only when the switch is on and the database is PostgreSQL."""
from unittest import skipIf, skipUnless

from django.conf import settings
from django.test import SimpleTestCase, TransactionTestCase


class SwitchTests(SimpleTestCase):
    @skipIf(settings.MULTI_SHOP, 'many shops is switched on')
    def test_nothing_changes_while_the_switch_is_off(self):
        self.assertNotIn('tenants', settings.INSTALLED_APPS)
        self.assertFalse(any('django_tenants' in m for m in settings.MIDDLEWARE))
        self.assertNotIn('django_tenants', settings.DATABASES['default']['ENGINE'])

    @skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
    def test_the_switch_installs_the_tenant_pieces(self):
        self.assertEqual(settings.MIDDLEWARE[0], 'django_tenants.middleware.main.TenantMainMiddleware')
        self.assertEqual(settings.MIDDLEWARE[1], 'tenants.middleware.ShopAccessMiddleware')
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django_tenants.postgresql_backend')
        self.assertIn('tenants', settings.INSTALLED_APPS)
        self.assertIn('.' + settings.SHOP_BASE_DOMAIN, settings.ALLOWED_HOSTS)      # every shop address is allowed
        self.assertIn('backend', settings.SHARED_APPS)             # the first shop's data stays on the main site
        self.assertIn('backend', settings.TENANT_APPS)             # and every new shop gets the same tables

    def test_prices(self):
        self.assertEqual(settings.PLAN_PRICES, {'monthly': 700, 'yearly': 8000})


@skipUnless(settings.MULTI_SHOP, 'many shops is switched off')
class SchemaIsolationTests(TransactionTestCase):
    def test_two_shops_do_not_share_data(self):
        from django_tenants.utils import schema_context

        from backend.models import Customer
        from tenants.models import Shop
        shops = [Shop(schema_name='s_test_a', name='A'), Shop(schema_name='s_test_b', name='B')]
        for s in shops:
            s.save()                                        # makes the schema and its tables
        try:
            with schema_context('s_test_a'):
                Customer.objects.create(name='Only in A')
                self.assertEqual(Customer.objects.count(), 1)
            with schema_context('s_test_b'):
                self.assertEqual(Customer.objects.count(), 0)
            with schema_context('public'):
                self.assertFalse(Customer.objects.filter(name='Only in A').exists())
        finally:
            for s in shops:
                s.delete(force_drop=True)
