"""Fast / slow / dead stock: the class of each medicine, the figures, and who may open the page."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from backend import movement
from backend.models import Product
from backend.tests.helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
T = today()


class ClassifyTests(SimpleTestCase):
    old = T - timedelta(days=400)

    def test_fast_normal_slow_by_how_long_the_stock_lasts(self):
        self.assertEqual(movement.classify(90, 30, 90, self.old, T)[0], 'fast')     # 1 a day, 30 left: 30 days
        self.assertEqual(movement.classify(90, 31, 90, self.old, T)[0], 'normal')
        self.assertEqual(movement.classify(90, 120, 90, self.old, T)[0], 'normal')
        self.assertEqual(movement.classify(90, 121, 90, self.old, T)[0], 'slow')

    def test_nothing_sold_is_dead_unless_the_stock_is_new(self):
        self.assertEqual(movement.classify(0, 50, 90, self.old, T), ('dead', None))
        self.assertEqual(movement.classify(0, 50, 90, T - timedelta(days=10), T), ('new', None))
        self.assertEqual(movement.classify(0, 50, 90, None, T)[0], 'dead')          # arrival unknown: treated as old stock
        self.assertEqual(movement.classify(5, 50, 90, T - timedelta(days=10), T)[0], 'slow')   # something sold: never new

    def test_period_is_one_of_the_allowed_ones(self):
        self.assertEqual(movement.period_from('30'), 30)
        for bad in (None, '', 'x', '-5', '45', '9' * 40, '0'):
            self.assertEqual(movement.period_from(bad), 90, bad)


@override_settings(**SAFE)
class MovementPageTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.c = self.client
        self.c.force_login(self.owner)
        # Dolo: bought long ago, sells fast.  Slowmed: bought long ago, 1 sold.  Deadmed: bought long ago, nothing sold.  Newmed: bought today.
        self.make = lambda name: Product.objects.create(name=name, unit=self.tablet, gst_rate=Decimal('12.00'))
        self.slow, self.dead, self.new = self.make('Slowmed'), self.make('Deadmed'), self.make('Newmed')
        for prod, qty, inv, days_ago in ((self.product, 200, 'A', 300), (self.slow, 400, 'B', 300), (self.dead, 100, 'C', 300), (self.new, 100, 'D', 0)):
            purchase, item = self.buy(qty=qty, price='100.00', unit='tablet', invoice=inv, product=prod)
            purchase.invoice_date = T - timedelta(days=days_ago)
            purchase.save()
        for _ in range(3):
            self.sell(qty=45, bill=self.new_bill(date=T - timedelta(days=5)))      # Dolo: 135 sold in 90 days, 65 left: 43 days -> normal
        self.sell(qty=1, product=self.slow, bill=self.new_bill(date=T - timedelta(days=5)))

    def rows(self, **params):
        r = self.c.get('/movement/', params)
        self.assertEqual(r.status_code, 200)
        return {x['name']: x for x in r.context['rows']}, r

    def test_each_medicine_gets_its_class_and_figures(self):
        rows, _ = self.rows()
        self.assertEqual(rows['Dolo 650']['cls'], 'normal')
        self.assertEqual(rows['Dolo 650']['sold'], 135)
        self.assertEqual(rows['Slowmed']['cls'], 'slow')
        self.assertEqual(rows['Deadmed']['cls'], 'dead')
        self.assertEqual(rows['Newmed']['cls'], 'new')
        self.assertIsNone(rows['Deadmed']['last_sold'])
        self.assertEqual(rows['Slowmed']['last_sold'], T - timedelta(days=5))
        self.assertTrue(rows['Deadmed']['value'] > 0)

    def test_dead_first_and_filter_by_class(self):
        rows, r = self.rows()
        self.assertEqual(list(rows)[0], 'Deadmed')
        only, _ = self.rows(show='dead')
        self.assertEqual(list(only), ['Deadmed'])
        every, _ = self.rows(show='rubbish')
        self.assertEqual(len(every), 4)

    def test_a_shorter_period_can_turn_a_medicine_dead(self):
        rows, _ = self.rows(days=30)
        self.assertEqual(rows['Slowmed']['cls'], 'slow')        # sold 5 days ago: inside 30 days
        self.assertEqual(rows['Deadmed']['cls'], 'dead')

    def test_out_of_stock_and_inactive_medicines_are_not_listed(self):
        Product.objects.filter(pk=self.dead.pk).update(is_active=False)
        rows, _ = self.rows()
        self.assertNotIn('Deadmed', rows)

    def test_a_long_list_is_cut_on_screen_but_not_in_the_file(self):
        from unittest import mock
        with mock.patch('backend.views_new.SHOW_MAX', 2):
            r = self.c.get('/movement/')
            self.assertEqual(len(r.context['rows']), 2)
            self.assertEqual(r.context['found'], 4)
            self.assertIn('Showing the first 2 of 4', r.content.decode())
            csv = self.c.get('/movement/', {'export': 'csv'}).content.decode('utf-8-sig')
        self.assertEqual(len([l for l in csv.splitlines() if l.strip()]), 5)      # header + all 4 medicines

    def test_staff_cannot_open_it(self):
        staff = get_user_model().objects.create_user('clerk', 'c@x.com', 'pw', is_staff=True)
        self.c.force_login(staff)
        self.assertEqual(self.c.get('/movement/').status_code, 403)
        self.c.logout()
        self.assertEqual(self.c.get('/movement/').status_code, 302)

    def test_export_gives_a_file(self):
        r = self.c.get('/movement/', {'export': 'csv', 'days': '90'})
        self.assertEqual(r.status_code, 200)
        text = r.content.decode('utf-8-sig')
        self.assertIn('Deadmed', text)
        self.assertIn('Dead', text)
        self.assertEqual(self.c.get('/movement/', {'export': 'xlsx'}).status_code, 200)

    def test_page_is_in_the_menu_for_the_owner(self):
        self.assertIn('/movement/', self.c.get('/movement/').content.decode())
