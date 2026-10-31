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
