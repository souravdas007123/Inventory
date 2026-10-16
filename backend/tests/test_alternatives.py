"""Same-salt alternatives and the hold / resume of a bill."""
import json
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import Batch, Bill, HeldBill, Product, norm_composition

from .helpers import StockMixin, today


class SaltKeyTests(TestCase):
    def test_same_salt_gives_the_same_key(self):
        same = ['Paracetamol 500mg', 'paracetamol 500 mg', 'PARACETAMOL  500MG', 'Paracetamol-500mg']
        self.assertEqual(len({norm_composition(x) for x in same}), 1)
        self.assertEqual(norm_composition('Paracetamol 500mg + Caffeine 50mg'), norm_composition('caffeine 50 mg, Paracetamol 500 mg'))

    def test_different_salts_or_strengths_differ(self):
        self.assertNotEqual(norm_composition('Paracetamol 500mg'), norm_composition('Paracetamol 650mg'))
        self.assertNotEqual(norm_composition('Paracetamol 500mg'), norm_composition('Paracetamol 500mg + Caffeine 50mg'))

    def test_blank_has_no_key(self):
        self.assertEqual(norm_composition(''), '')
        self.assertEqual(norm_composition(None), '')


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class AlternativesTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.product.composition = 'Paracetamol 650mg'
        self.product.save()                                                       # Dolo 650: no stock
        self.dear = self.make('Calpol 650', 'paracetamol 650 mg', mrp='4.00')
        self.cheap = self.make('Paracip 650', 'Paracetamol 650mg', mrp='2.50')
        self.old = self.make('Crocin 650', 'Paracetamol 650mg', mrp='1.00')       # same salt, but only an expired batch
        Batch.objects.filter(product=self.old).update(expire_date=today().replace(year=today().year - 1))
        self.other = self.make('Brufen 400', 'Ibuprofen 400mg', mrp='1.00')       # another salt
        self.nostock = Product.objects.create(name='Pacimol 650', composition='Paracetamol 650mg', unit=self.tablet, gst_rate=Decimal('12.00'))

    def make(self, name, comp, mrp):
        p = Product.objects.create(name=name, composition=comp, unit=self.tablet, gst_rate=Decimal('12.00'), mrp=Decimal(mrp))
        self.buy(qty=5, invoice='I-' + name, product=p, unit='tablet', price='56.00')
        return Product.objects.get(pk=p.pk)

    def test_only_sellable_medicines_with_the_same_salt_cheapest_first(self):
        alts = self.product.get_alternatives(limit=5)
        self.assertEqual([a.pk for a in alts], [self.cheap.pk, self.dear.pk])
        self.assertNotIn(self.old.pk, [a.pk for a in alts])           # expired stock cannot be sold
        self.assertNotIn(self.other.pk, [a.pk for a in alts])
        self.assertNotIn(self.nostock.pk, [a.pk for a in alts])
        self.assertEqual(len(self.product.get_alternatives()), 2)

    def test_no_composition_means_no_alternative(self):
        self.assertEqual(self.other.get_alternatives(), [])
        self.assertEqual(Product.objects.create(name='X', unit=self.tablet, gst_rate=Decimal('5.00')).get_alternatives(), [])

    def test_expired_only_stock_counts_as_not_available(self):
        self.assertEqual(self.old.stock_qty, 5)
        self.assertEqual(self.old.available_qty(), 0)
        self.assertEqual(self.cheap.available_qty(), 5)
        self.assertEqual(self.product.available_qty(), 0)

    def test_billing_lookup_gives_the_alternatives_of_a_picked_product(self):
        d = self.client.get(reverse('product_lookup'), {'id': self.product.pk}).json()['results'][0]
        self.assertEqual(d['available_qty'], 0)
        self.assertEqual([a['id'] for a in d['alts']], [self.cheap.pk, self.dear.pk])
        self.assertTrue(all(a['available_qty'] > 0 and a['stock'] for a in d['alts']))
        d = self.client.get(reverse('product_lookup'), {'q': 'Calpol'}).json()['results'][0]
        self.assertNotIn('alts', d)                                    # a search does not run the (heavier) alternative lookup
        self.assertEqual(d['available_qty'], 5)


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class HoldBillTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.buy(qty=5)

    def fields(self, **extra):
        data = {'customer_name': 'Ramesh', 'date': today().isoformat(), 'invoice_mode': 'offline', 'payment_mode': 'CASH', 'amount_paid': '0',
                'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
                'items-0-product': str(self.product.pk), 'items-0-qty': '3', 'items-0-rate': '10.00',
                'items-1-product': '', 'items-1-qty': '', 'csrfmiddlewaretoken': 'x'}
        data.update(extra)
        return [[k, v] for k, v in data.items()]

    def hold(self, fields):
        return self.client.post(reverse('bill_hold'), json.dumps({'fields': fields}), content_type='application/json')

    def test_hold_parks_the_bill_without_touching_stock_or_bills(self):
        before = self.stock()
        resp = self.hold(self.fields())
        self.assertEqual(resp.status_code, 200, resp.content)
        d = resp.json()
        self.assertTrue(d['ok'])
        self.assertEqual((d['count'], d['label']), (1, 'Ramesh'))
        held = HeldBill.objects.get(pk=d['id'])
        self.assertNotIn('csrfmiddlewaretoken', [n for n, _ in held.payload])
        self.assertEqual(Bill.objects.count(), 0)
        self.assertEqual(self.stock(), before)

    def test_an_empty_bill_cannot_be_held(self):
        resp = self.hold(self.fields(**{'items-0-product': ''}))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(HeldBill.objects.count(), 0)

    def test_bad_data_is_refused(self):
        for bad in ('not json', json.dumps({'fields': 'x'}), json.dumps({'fields': [['a']]}), json.dumps({'fields': [['a', 5]]}), json.dumps({})):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.post(reverse('bill_hold'), bad, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(reverse('bill_hold')).status_code, 405)

    def test_the_new_bill_page_lists_held_bills_and_resume_brings_the_data_back(self):
        held_id = self.hold(self.fields()).json()['id']
        html = self.client.get(reverse('new', kwargs={'key': 'bills'})).content.decode()
        self.assertIn('Bills on hold (1)', html)
        self.assertIn(f'?resume={held_id}', html)
        self.assertNotIn('id="held-resume"', html)
        html = self.client.get(reverse('new', kwargs={'key': 'bills'}), {'resume': held_id}).content.decode()
        data = json.loads(re.search(r'<script id="held-resume" type="application/json">(.*?)</script>', html, re.S).group(1))
        self.assertIn(['items-0-qty', '3'], data)
        self.assertIn(['customer_name', 'Ramesh'], data)
        self.assertIn(f'name="held_id" value="{held_id}"', html)

    def test_saving_a_resumed_bill_removes_it_from_hold(self):
        held_id = self.hold(self.fields()).json()['id']
        post = dict(self.fields())
        post.pop('csrfmiddlewaretoken')
        post['held_id'] = str(held_id)
        resp = self.client.post(reverse('new', kwargs={'key': 'bills'}), post)
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and resp.context['form'].errors)
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(self.stock(), 50 - 3)
        self.assertFalse(HeldBill.objects.filter(pk=held_id).exists())

    def test_a_failed_save_keeps_the_held_bill(self):
        held_id = self.hold(self.fields()).json()['id']
        post = dict(self.fields(**{'items-0-qty': '9999'}))
        post.pop('csrfmiddlewaretoken')
        post['held_id'] = str(held_id)
        resp = self.client.post(reverse('new', kwargs={'key': 'bills'}), post)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Bill.objects.count(), 0)
        self.assertTrue(HeldBill.objects.filter(pk=held_id).exists())
        self.assertIn(f'name="held_id" value="{held_id}"', resp.content.decode())

    def test_discard(self):
        held_id = self.hold(self.fields()).json()['id']
        self.assertEqual(self.client.get(reverse('bill_hold_discard', args=[held_id])).status_code, 405)
        self.assertEqual(self.client.post(reverse('bill_hold_discard', args=[held_id])).status_code, 302)
        self.assertEqual(HeldBill.objects.count(), 0)
        self.assertEqual(self.client.post(reverse('bill_hold_discard', args=[held_id])).status_code, 302)   # again: harmless

    def test_a_user_who_cannot_add_bills_cannot_hold(self):
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345')
        self.client.force_login(clerk)
        self.assertEqual(self.hold(self.fields()).status_code, 403)

    def test_there_is_a_limit(self):
        for _ in range(30):
            HeldBill.objects.create(label='x', items_count=1, payload=[])
        self.assertEqual(self.hold(self.fields()).status_code, 400)
