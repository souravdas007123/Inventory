"""Security fixes: admin import/export needs permission, and the sign-in lock cannot be dodged with a made-up X-Forwarded-For."""
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from backend.models import Product, ShopProfile, Supplier, Unit
from backend.security import client_ip

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


def export_data(html):
    import re
    data = {n: 'on' for n in re.findall(r'name="([a-z]+_[a-z_]+)"', html)}
    data['format'] = '0'
    return data


@override_settings(**SAFE)
class AdminImportExportTests(TestCase):
    def setUp(self):
        U = get_user_model()
        unit = Unit.objects.create(name='Tab')
        Product.objects.create(name='SecretMed', unit=unit)
        Supplier.objects.create(company='Hidden Supplier', phone='999')
        self.nobody = U.objects.create_user('nobody', password='pw', is_staff=True)

    def login(self, user):
        self.client.force_login(user)

    def test_a_staff_user_without_permission_cannot_export(self):
        self.login(self.nobody)
        for model in ('product', 'supplier', 'brand', 'category', 'unit'):
            self.assertEqual(self.client.get(f'/admin/backend/{model}/export/').status_code, 403, model)
        r = self.client.post('/admin/backend/supplier/export/', {'format': '0'})
        self.assertNotIn(b'Hidden Supplier', r.content)

    def test_a_staff_user_without_permission_cannot_import(self):
        self.login(self.nobody)
        up = SimpleUploadedFile('a.csv', b'name,unit\nHACKED,Tab\n')
        r = self.client.post('/admin/backend/product/import/', {'format': '0', 'import_file': up})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post('/admin/backend/product/process_import/', {}).status_code, 403)
        self.assertFalse(Product.objects.filter(name='HACKED').exists())

    def test_the_right_permission_opens_export_and_import(self):
        self.nobody.user_permissions.add(Permission.objects.get(codename='view_supplier'), Permission.objects.get(codename='add_supplier'))
        self.login(self.nobody)
        g = self.client.get('/admin/backend/supplier/export/')
        self.assertEqual(g.status_code, 200)
        p = self.client.post('/admin/backend/supplier/export/', export_data(g.content.decode()))
        self.assertIn(b'Hidden Supplier', p.content)
        self.assertEqual(self.client.get('/admin/backend/supplier/import/').status_code, 200)
        self.assertEqual(self.client.get('/admin/backend/product/export/').status_code, 403)     # no permission for products

    def test_an_owner_can_still_export(self):
        boss = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.login(boss)
        self.assertEqual(self.client.get('/admin/backend/product/export/').status_code, 200)

    def test_shop_settings_cannot_be_added_without_permission(self):
        self.login(self.nobody)
        self.assertFalse(ShopProfile.objects.exists())
        self.assertEqual(self.client.get('/admin/backend/shopprofile/add/').status_code, 403)


class ClientIpTests(TestCase):
    def ip(self, forwarded, hops=None, proxy='1'):
        env = {'DJANGO_BEHIND_PROXY': proxy}
        if hops:
            env['DJANGO_PROXY_HOPS'] = hops
        req = RequestFactory().get('/', HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR='127.0.0.1')
        with mock.patch.dict(os.environ, env):
            return client_ip(req)

    def test_a_made_up_first_address_is_ignored(self):
        self.assertEqual(self.ip('6.6.6.6, 203.0.113.7'), '203.0.113.7')          # the proxy wrote the last one

    def test_a_single_address_is_used(self):
        self.assertEqual(self.ip('203.0.113.7'), '203.0.113.7')

    def test_two_proxies_in_front(self):
        self.assertEqual(self.ip('6.6.6.6, 203.0.113.7, 10.0.0.2', hops='2'), '203.0.113.7')

    def test_the_header_is_not_trusted_without_a_proxy(self):
        self.assertEqual(self.ip('6.6.6.6', proxy='0'), '127.0.0.1')
