"""Reorder Suggestions: the Auto view lists every product that needs ordering under any sales period (15/30/60/90 days)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from backend import views_extra
from backend.models import Product, ProductUnit
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


class OnlyPossibleProductsTests(StockMixin, TestCase):
    """The suggestion loads only products that can possibly be suggested; the result must equal the one made from ALL products."""

    def everything(self, days, cover):
        """The old way: every active product goes through reorder.suggest()."""
        from django.db.models import Sum
        from backend import reorder
        from backend.models import Batch, InvoiceItem, PurchaseOrderItem
        t = today()
        windows = views_extra.REORDER_WINDOWS if days == views_extra.AUTO else (days,)
        products = []
        for p in Product.objects.exclude(is_active=False).select_related('unit').prefetch_related('unit_conversions__unit'):
            packs = [c for c in p.unit_conversions.all() if c.factor > 1]
            pack = max(packs, key=lambda c: c.factor) if packs else None
            products.append(dict(id=p.pk, name=p.name or str(p), unit=p.unit.name if p.unit_id else '', stock_qty=p.stock_qty,
                                 reorder_level=p.reorder_level, pack_name=pack.unit.name if pack else '', pack_factor=pack.factor if pack else 0))
        expired = {r['product_id']: r['s'] for r in Batch.objects.filter(qty__gt=0, expire_date__lt=t, product__isnull=False)
                   .values('product_id').annotate(s=Sum('qty'))}
        on_order = {r['product_id']: r['s'] for r in PurchaseOrderItem.objects.filter(purchase_order__status__in=['PENDING', 'SENT'])
                    .values('product_id').annotate(s=Sum('order_qty'))}
        best = {}
        for w in windows:
            sold = {r['product_id']: r['s'] for r in InvoiceItem.objects.filter(
                bill__status='FINAL', bill__date__gte=t - timedelta(days=w), product__isnull=False).values('product_id').annotate(s=Sum('base_qty'))}
            for r in reorder.suggest(products, sold, expired, on_order, days=w, cover=cover):
                if r['id'] not in best or r['per_day'] > best[r['id']]['per_day']:
                    best[r['id']] = r
        return {pid: (r['qty'], r['status']) for pid, r in best.items()}

    def make_products(self):
        from decimal import Decimal
        from backend.models import Product as P
        made = {}
        for name, level in (('fast', 0), ('old sale', 0), ('never sold low', 50), ('never sold plenty', 0), ('expired only', 30), ('inactive low', 50)):
            made[name] = P.objects.create(name=name, unit=self.tablet, gst_rate=Decimal('12.00'), reorder_level=level)
            ProductUnit.objects.create(product=made[name], unit=self.strip, factor=10)
        return made

    def stock_up(self, product, strips, days=300, invoice='X'):
        self.buy(qty=strips, price='1000.00', unit='strip', days=days, invoice=invoice, product=product)

    def test_same_result_as_looking_at_every_product(self):
        from backend.models import Batch
        made = self.make_products()
        for n, (name, p) in enumerate(made.items()):
            self.stock_up(p, 30 if name != 'never sold low' else 1, invoice=f'S{n}')
        self.sell(qty=200, product=made['fast'], bill=self.new_bill(date=today() - timedelta(days=3)))
        self.sell(qty=50, product=made['old sale'], bill=self.new_bill(date=today() - timedelta(days=80)))
        Batch.objects.filter(product=made['expired only']).update(expire_date=today() - timedelta(days=5))
        Product.objects.filter(pk=made['inactive low'].pk).update(is_active=False, stock_qty=1)
        for days in (views_extra.AUTO, 15, 30, 60, 90):
            for cover in (15, 30, 60):
                got = {r['id']: (r['qty'], r['status']) for r in views_extra._reorder_suggestions(days, cover, today())}
                self.assertEqual(got, self.everything(days, cover), (days, cover))
        names = {r['name'] for r in views_extra._reorder_suggestions(views_extra.AUTO, 30, today())}
        self.assertIn('fast', names)
        self.assertIn('expired only', names)
        self.assertNotIn('inactive low', names)
        self.assertNotIn('never sold plenty', names)
