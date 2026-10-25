"""Owner data export: a zip of CSV files, owner only, safe for Excel."""
import csv
import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from backend import dataexport
from backend.models import Category, ShopProfile
from backend.models_extra import AuditLog

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)


@override_settings(**SAFE)
class DataExportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345-xx')

    def test_the_owner_gets_a_zip_with_one_csv_per_table_and_a_readme(self):
        ShopProfile.objects.create(name='Test Pharmacy')
        Category.objects.create(name='Tablets')
        self.client.force_login(self.owner)
        resp = self.client.post('/backup/export/')
        self.assertEqual((resp.status_code, resp['Content-Type']), (200, 'application/zip'))
        self.assertIn('medsync-data-', resp['Content-Disposition'])
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        names = z.namelist()
        for n in ('product.csv', 'bill.csv', 'category.csv', 'README.txt'):
            self.assertIn(n, names)
        for n in ('blogpost.csv', 'auditlog.csv', 'importlayout.csv'):
            self.assertNotIn(n, names)
        rows = list(csv.reader(io.StringIO(z.read('category.csv').decode('utf-8-sig'))))
        self.assertEqual(rows[1][1], 'Tablets')
        self.assertIn('Test Pharmacy', z.read('README.txt').decode())

    def test_it_is_written_to_the_audit_log(self):
        self.client.force_login(self.owner)
        self.client.post('/backup/export/')
        self.assertTrue(AuditLog.objects.filter(action='EXPORT', user='owner').exists())

    def test_a_second_download_within_a_minute_is_refused_politely(self):
        self.client.force_login(self.owner)
        self.client.post('/backup/export/')
        resp = self.client.post('/backup/export/', follow=True)
        self.assertContains(resp, 'Please wait a minute')

    def test_get_does_not_download_and_visitors_and_clerks_are_kept_out(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/backup/export/').status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.post('/backup/export/').status_code, 302)
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345-xx')
        self.client.force_login(clerk)
        resp = self.client.post('/backup/export/')
        self.assertNotEqual(resp.get('Content-Type'), 'application/zip')

    def test_the_backup_page_has_the_button(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/backup/'), 'Download my data')


class SafeValueTests(TestCase):
    def test_formula_text_is_neutralised_but_numbers_and_empty_values_are_kept(self):
        self.assertEqual(dataexport.safe('=cmd|calc'), "'=cmd|calc")
        self.assertEqual(dataexport.safe('@SUM(A1)'), "'@SUM(A1)")
        self.assertEqual(dataexport.safe('-5.50'), '-5.50')
        self.assertEqual(dataexport.safe(None), '')
        self.assertEqual(dataexport.safe(True), 'Yes')
        self.assertEqual(dataexport.safe('Paracetamol'), 'Paracetamol')
