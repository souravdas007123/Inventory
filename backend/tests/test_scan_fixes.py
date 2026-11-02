"""Bugs found by the full-app scan."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class LastPurchaseTests(TestCase):
    def setUp(self):
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_a_huge_or_negative_supplier_id_is_just_not_found(self):
        for sid in ('99999999999999999999999', '-5', '0', 'abc'):
            r = self.c.get('/api/last_purchase/', {'supplier': sid, 'product': '1'})
            self.assertEqual(r.status_code, 200, sid)
            self.assertEqual(r.json(), {'found': False}, sid)


from backend.models import PurchaseItem          # noqa: E402
from backend.tests.helpers import StockMixin       # noqa: E402


@override_settings(**SAFE)
class RemoveRowButtonTests(StockMixin, TestCase):
    """Every row of a bill / purchase has a ✕ button; the old "Delete" tick box column is hidden (it was out of sight in
    the purchase form when "More details" was closed)."""

    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_new_bill_and_purchase_rows_have_a_cross(self):
        for url in ('/bills/new/', '/purchases/new/'):
            page = self.c.get(url).content.decode()
            self.assertEqual(page.count('class="rowdel"'), 2, url)         # the first row and the template of a new row
            self.assertIn('class="delcell"', page)                          # the cross sits in the last column (the tick box is hidden)

    def test_a_saved_row_can_still_be_removed_through_the_hidden_tick(self):
        purchase, item = self.buy(qty=4, invoice='X-1')
        other = PurchaseItem.objects.create(purchase=purchase, product=self.product, qty=2, unit=self.strip,
                                            purchase_price='100.00', expire_date=item.expire_date)
        page = self.c.get('/purchases/%d/' % purchase.pk).content.decode()
        self.assertEqual(page.count('class="rowdel"'), 4)                  # 2 saved rows + 1 empty row + the template
        data = {'supplier': self.supplier.pk, 'invoice_number': 'X-1', 'invoice_date': purchase.invoice_date,
                'items-TOTAL_FORMS': 2, 'items-INITIAL_FORMS': 2, 'items-MIN_NUM_FORMS': 0, 'items-MAX_NUM_FORMS': 1000}
        for i, it in enumerate((item, other)):
            data.update({'items-%d-id' % i: it.pk, 'items-%d-purchase' % i: purchase.pk, 'items-%d-product' % i: self.product.pk,
                         'items-%d-qty' % i: it.qty, 'items-%d-unit' % i: self.strip.pk,
                         'items-%d-purchase_price' % i: it.purchase_price, 'items-%d-expire_date' % i: item.expire_date})
        data['items-1-DELETE'] = 'on'
        r = self.c.post('/purchases/%d/' % purchase.pk, data)
        self.assertEqual(r.status_code, 302, getattr(r, 'content', b'')[:300])
        self.assertEqual(list(purchase.items.values_list('pk', flat=True)), [item.pk])
