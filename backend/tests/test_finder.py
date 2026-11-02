"""Medicine Finder: same-salt medicines, in stock first and cheapest first."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.models import Product
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class FinderTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))
        self.product.composition = 'Paracetamol 650mg'
        self.product.mrp = Decimal('3.00')
        self.product.save()
        def make(name, comp, mrp, stock=True):
            p = Product.objects.create(name=name, unit=self.tablet, gst_rate=Decimal('12.00'), composition=comp, mrp=Decimal(mrp))
            if stock:
                self.buy(qty=50, price='100.00', unit='tablet', product=p, invoice='I-' + name)
            return p
        self.dear = make('Calpol 650', 'paracetamol 650 mg', '4.00')
        self.cheap = make('Paracip 650', 'Paracetamol 650mg', '2.50')
        self.out = make('Crocin 650', 'Paracetamol 650mg', '1.00', stock=False)
        self.other = make('Dolo 500', 'Paracetamol 500mg', '1.00')        # a different strength: not a substitute
        self.none = make('Mystery', '', '1.00')

    def result(self, q):
        r = self.client.get('/finder/', {'q': q})
        self.assertEqual(r.status_code, 200)
        return r.context['results']

    def test_substitutes_in_stock_first_then_cheapest(self):
        res = self.result('Dolo 650')
        self.assertEqual(len(res), 1)
        self.assertEqual([s.name for s in res[0]['subs']], ['Paracip 650', 'Calpol 650', 'Crocin 650'])

    def test_a_different_strength_or_no_composition_gives_none(self):
        self.assertNotIn('Dolo 500', [s.name for s in self.result('Dolo 650')[0]['subs']])
        self.assertEqual(self.result('Mystery')[0]['subs'], [])
        self.assertIn('Write the composition', self.client.get('/finder/', {'q': 'Mystery'}).content.decode())

    def test_search_by_salt_finds_every_medicine_with_it(self):
        names = {r['product'].name for r in self.result('paracetamol')}
        self.assertTrue({'Dolo 650', 'Calpol 650'} <= names)

    def test_inactive_medicines_are_hidden(self):
        Product.objects.filter(pk=self.cheap.pk).update(is_active=False)
        self.assertNotIn('Paracip 650', [s.name for s in self.result('Dolo 650')[0]['subs']])
        self.assertEqual(self.result('Paracip'), [])

    def test_empty_unknown_and_odd_searches_are_safe(self):
        self.assertEqual(self.result(''), [])
        self.assertIn('No medicine found', self.client.get('/finder/', {'q': 'zzzzzz'}).content.decode())
        for odd in ("' OR 1=1 --", '<script>alert(1)</script>', 'x' * 400, '%'):
            self.assertEqual(self.client.get('/finder/', {'q': odd}).status_code, 200)
        self.assertNotIn('<script>alert(1)</script>', self.client.get('/finder/', {'q': '<script>alert(1)</script>'}).content.decode())

    def test_needs_a_login_and_product_permission(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/finder/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/finder/').status_code, 302)
