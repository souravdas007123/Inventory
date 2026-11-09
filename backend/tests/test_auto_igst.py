"""IGST follows the customer's state: another state than the shop's => IGST, otherwise CGST + SGST."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings

from backend import gstin as g
from backend.models import GST_STATE_CODES, ShopProfile
from backend.models_extra import Customer
from backend.tests.helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


def make_gstin(state, pan='ABCDE1234F', entity='1'):
    first14 = f'{state}{pan}{entity}Z'
    return first14 + g.check_char(first14)


MH, KA = make_gstin('27'), make_gstin('29')          # Maharashtra, Karnataka


class StateRuleTests(SimpleTestCase):
    def test_state_code(self):
        self.assertEqual(g.state_code(MH, GST_STATE_CODES), '27')
        self.assertEqual(g.state_code(' ' + MH.lower() + ' ', GST_STATE_CODES), '27')
        for bad in ('', None, '27ABCDE', 'x' * 15, '98ABCDE1234F1Z5'):
            self.assertEqual(g.state_code(bad, GST_STATE_CODES), '')

    def test_inter_state(self):
        self.assertTrue(g.is_inter_state(MH, KA, GST_STATE_CODES))
        self.assertFalse(g.is_inter_state(MH, make_gstin('27', pan='PQRST9876K'), GST_STATE_CODES))     # same state

    def test_nothing_is_guessed_when_a_gstin_is_missing(self):
        self.assertFalse(g.is_inter_state('', KA, GST_STATE_CODES))
        self.assertFalse(g.is_inter_state(MH, '', GST_STATE_CODES))
        self.assertFalse(g.is_inter_state(MH, 'junk', GST_STATE_CODES))
        self.assertFalse(g.is_inter_state(None, None, GST_STATE_CODES))


@override_settings(**SAFE)
class CustomerLookupIgstTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))
        shop = ShopProfile.get()
        shop.gstin = MH
        shop.save()
        self.far = Customer.objects.create(name='Bengaluru Med', phone='9000000001', gstin=KA)
        self.near = Customer.objects.create(name='Pune Med', phone='9000000002', gstin=make_gstin('27', pan='PQRST9876K'))
        self.walk = Customer.objects.create(name='Walk In', phone='9000000003')

    def get(self, c):
        return self.c.get('/api/customer_lookup/', {'id': c.pk}).json()

    def test_other_state_customer_gets_igst(self):
        d = self.get(self.far)
        self.assertTrue(d['igst'])
        self.assertEqual(d['state'], 'Karnataka')
        self.assertTrue(d['shop_gstin'])

    def test_same_state_and_no_gstin_customers_do_not(self):
        self.assertFalse(self.get(self.near)['igst'])
        d = self.get(self.walk)
        self.assertFalse(d['igst'])
        self.assertEqual(d['state'], '')

    def test_shop_without_gstin_applies_nothing_and_says_so(self):
        ShopProfile.objects.update(gstin='')
        d = self.get(self.far)
        self.assertFalse(d['igst'])
        self.assertFalse(d['shop_gstin'])
        self.assertEqual(d['state'], 'Karnataka')            # the screen can hint "add your shop GSTIN"


@override_settings(**SAFE)
class BillScreenIgstWiringTests(StockMixin, TestCase):
    def test_new_bill_page_has_the_automatic_igst_code(self):
        c = Client()
        c.force_login(get_user_model().objects.create_superuser('boss2', 'b2@x.com', 'pw'))
        html = c.get('/bills/new/').content.decode()
        for needle in ('function setAutoIgst', 'dataset.manual', 'e.isTrusted', 'IGST applied'):
            self.assertIn(needle, html)
