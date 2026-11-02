"""Reorder Suggestions: the Auto view lists every product that needs ordering under any sales period (15/30/60/90 days)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from backend import views_extra
from backend.models import Product
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class AutoReorderTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))
        # 220 tablets bought, 120 sold 5 days ago in a rush: 100 left. Last 15 days: 8 a day -> lasts 12 days (order soon).
        # Over 30 days it is 4 a day -> lasts 25 days, so the 30 day view alone shows nothing.
        self.buy(qty=22, price='1000.00', unit='strip')
        self.sell(qty=120, bill=self.new_bill(date=today() - timedelta(days=5)))
        Product.objects.filter(pk=self.product.pk).update(reorder_level=0)

    def names(self, **params):
        r = self.c.get('/reorder/', params)
        self.assertEqual(r.status_code, 200)
        return [row['name'] for row in r.context['rows']], r

    def test_one_period_alone_can_miss_a_product(self):
        self.assertEqual(self.names(days=30)[0], [])
        self.assertEqual(self.names(days=15)[0], ['Dolo 650'])

    def test_auto_is_the_default_and_shows_it_with_the_period_that_found_it(self):
        for params in ({}, {'days': 'auto'}, {'days': 'rubbish'}):
            names, r = self.names(**params)
            self.assertEqual(names, ['Dolo 650'], params)
            row = r.context['rows'][0]
            self.assertEqual(row['window'], 15)
            self.assertIn('Auto (all products)', r.content.decode())
            self.assertIn('last 15 days', r.content.decode())

    def test_auto_never_hides_what_any_single_period_shows(self):
        union = set()
        for w in views_extra.REORDER_WINDOWS:
            union |= {r['id'] for r in views_extra._reorder_suggestions(w, 30, today())}
        auto = {r['id'] for r in views_extra._reorder_suggestions(views_extra.AUTO, 30, today())}
        self.assertEqual(auto, union)
        self.assertEqual(len(views_extra._reorder_suggestions(views_extra.AUTO, 30, today())), len(auto))   # each product once

    def test_plenty_of_stock_shows_nothing_and_says_so(self):
        self.buy(qty=500, price='1000.00', unit='strip', invoice='INV-2')
        names, r = self.names()
        self.assertEqual(names, [])
        self.assertIn('Nothing needs to be ordered', r.content.decode())
