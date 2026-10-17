"""Live amount preview on the bill and purchase screens: the GST-rate lookup, the page wiring, and (when Node.js is
installed) a check that the preview's arithmetic gives exactly the amounts the real models save."""
import json
import random
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from backend.models import InvoiceItem, Product

from .helpers import StockMixin

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
JS = Path(settings.BASE_DIR) / 'templates' / 'app' / 'live_calc.js'

NODE_SCRIPT = r"""
const c = require(process.argv[1]);
const B = BigInt, cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const m = x => c.money(x).replace(/,/g, '');
const out = cases.map(k => {
  if (k.kind === 'bill') {
    const L = c.billLine(B(k.qty), c.paise(k.rate), c.paise(k.pct), c.paise(k.flat), B(k.g), k.igst);
    return [L.taxable, L.gst, L.cgst, L.sgst, L.igst, L.total].map(m);
  }
  const P = c.purchaseLine(c.paise(k.price), B(k.g));
  return [P.taxable, P.gst, P.cgst, P.sgst].map(m);
});
process.stdout.write(JSON.stringify(out));
"""


@override_settings(**SAFE)
class PreviewPagesTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xyz'))

    def test_gst_rates_are_whole_percent_and_junk_ids_are_ignored(self):
        other = Product.objects.create(name='Other', unit=self.tablet, gst_rate=Decimal('5.00'))
        r = self.client.get('/api/gst_rates/', {'ids': f'{self.product.pk},{other.pk},abc,,99999999,1;2'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'rates': {str(self.product.pk): 12, str(other.pk): 5}})
        self.assertEqual(self.client.get('/api/gst_rates/').json(), {'rates': {}})

    def test_gst_rates_need_a_login(self):
        self.client.logout()
        self.assertNotEqual(self.client.get('/api/gst_rates/', {'ids': str(self.product.pk)}).status_code, 200)

    def test_bill_and_purchase_forms_have_the_preview(self):
        for key in ('bills', 'purchases'):
            html = self.client.get(reverse('new', kwargs={'key': key})).content.decode()
            self.assertIn('id="live"', html, key)
            self.assertIn(f'data-kind="{key}"', html, key)
            self.assertIn('MedCalc', html, key)
        self.assertEqual(self.client.get(reverse('new', kwargs={'key': 'expenses'})).content.decode().count('MedCalc'), 0)

    def test_a_saved_purchase_keeps_its_own_gst_rate_in_the_preview(self):
        purchase, item = self.buy()
        Product.objects.filter(pk=self.product.pk).update(gst_rate=Decimal('5.00'))    # the product changes later
        html = self.client.get(reverse('edit', kwargs={'key': 'purchases', 'pk': purchase.pk})).content.decode()
        self.assertIn('id="saved-gst"', html)
        self.assertIn(f'"{item.pk}": {{"p": {self.product.pk}, "g": 12}}', html)


@override_settings(**SAFE)
class PreviewMatchesSavedAmountsTests(StockMixin, TestCase):
    """The same inputs go through the preview's JavaScript and through the real models: the amounts must be identical."""

    def test_amounts_match_the_models(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is not installed here')
        rng = random.Random(11)
        products = {g: Product.objects.create(name=f'P{g}', unit=self.tablet, gst_rate=Decimal(g)) for g in (0, 5, 12, 18, 28)}
        for n, p in enumerate(products.values()):
            self.buy(qty=100000, price='1000.00', unit='tablet', product=p, invoice=f'S-{n}')
        cases, saved = [], []
        for i in range(60):
            g = rng.choice(list(products))
            mode = rng.choice(['none', 'pct', 'flat'])
            case = dict(kind='bill', qty=rng.randint(1, 40), rate=f'{rng.randint(1, 900000) / 100:.2f}', g=g, igst=rng.random() < .3,
                        pct=f'{rng.randint(0, 10000) / 100:.2f}' if mode == 'pct' else '0',
                        flat=f'{rng.randint(0, 3000) / 100:.2f}' if mode == 'flat' else '0')
            item = InvoiceItem.objects.create(
                bill=self.new_bill(), product=products[g], qty=case['qty'], rate=Decimal(case['rate']), is_igst=case['igst'],
                discount_percent=Decimal(case['pct']) or None, discount_amount=Decimal(case['flat']) or None)
            cases.append(case)
            saved.append([str(x) for x in (item.taxable_value, item.gst, item.cgst, item.sgst, item.igst, item.total)])
        for i in range(60):
            g = rng.choice(list(products))
            case = dict(kind='purchase', price=f'{rng.randint(1, 2000000) / 100:.2f}', g=g)
            _, pitem = self.buy(qty=rng.randint(1, 20), price=case['price'], unit='tablet', product=products[g], invoice=f'P-{i}')
            cases.append(case)
            saved.append([str(x) for x in (pitem.tax, pitem.gst, pitem.cgst, pitem.sgst)])
        result = subprocess.run([node, '-e', NODE_SCRIPT, str(JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), saved)
