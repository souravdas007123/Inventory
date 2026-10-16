"""The health score: real findings, honest scoring, the page and the admin block."""
import gzip
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from backend import health
from backend.models import Batch, HealthSnapshot, Product

from .helpers import StockMixin, today

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


def find(checks, title):
    return next(c for c in checks if c['title'] == title)


class ScoringTests(SimpleTestCase):
    def test_labels(self):
        self.assertEqual(health.label_for(95), ('Excellent', 'green'))
        self.assertEqual(health.label_for(80), ('Good', 'green'))
        self.assertEqual(health.label_for(65), ('Needs attention', 'amber'))
        self.assertEqual(health.label_for(40), ('At risk', 'red'))

    def test_section_score_loses_the_points_and_never_goes_below_zero(self):
        self.assertEqual(health.section_score([health.chk('ok', 'a', ''), health.chk('warn', 'b', '', 15)]), 85)
        self.assertEqual(health.section_score([health.chk('bad', 'a', '', 80), health.chk('bad', 'b', '', 80)]), 0)

    def test_a_check_that_could_not_run_is_not_counted_as_good(self):
        self.assertIsNone(health.section_score([health.chk('unknown', 'a', '')]))
        self.assertEqual(health.section_score([health.chk('unknown', 'a', ''), health.chk('warn', 'b', '', 10)]), 90)

    def test_weights_add_up_to_100(self):
        self.assertEqual(sum(health.WEIGHTS.values()), 100)


class BackupCheckTests(SimpleTestCase):
    def make_db(self, folder, name, good=True):
        raw = Path(folder) / 'x.sqlite3'
        if good:
            con = sqlite3.connect(raw)
            con.execute('create table t (a int)')
            con.commit()
            con.close()
            data = raw.read_bytes()
        else:
            data = b'this is not a database' * 100
        path = Path(folder) / name
        with gzip.open(path, 'wb') as f:
            f.write(data)
        return path

    def test_a_good_backup_passes_and_a_damaged_one_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(health._verify_backup(self.make_db(tmp, 'medsync_20261016_010101.sqlite3.gz')), '')
            self.assertNotEqual(health._verify_backup(self.make_db(tmp, 'medsync_20261016_020202.sqlite3.gz', good=False)), '')

    def test_backup_age_decides_the_finding(self):
        def run(age):
            with tempfile.TemporaryDirectory() as tmp:
                when = datetime.now() - age
                self.make_db(tmp, f'medsync_{when:%Y%m%d_%H%M%S}.sqlite3.gz')
                with override_settings(BACKUP_DIR=tmp):
                    return health.safety_checks()
        fresh = run(timedelta(hours=2))
        self.assertEqual(find(fresh, 'Recent backup')['status'], 'ok')
        self.assertEqual(find(fresh, 'Backup opens correctly')['status'], 'ok')
        self.assertEqual(find(run(timedelta(days=2)), 'Backup is getting old')['lost'], 15)
        self.assertEqual(find(run(timedelta(days=9)), 'Backup is old')['lost'], 40)

    def test_no_backup_at_all(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(BACKUP_DIR=tmp):
            c = find(health.safety_checks(), 'No backup yet')
        self.assertEqual((c['status'], c['lost']), ('bad', 50))

    def test_debug_mode_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(BACKUP_DIR=tmp, DEBUG=True):
            self.assertEqual(find(health.safety_checks(), 'Debug mode is ON')['status'], 'bad')


@override_settings(**SAFE)
class DataCheckTests(StockMixin, TestCase):
    def test_a_clean_shop_has_no_data_findings(self):
        self.buy(qty=5)
        checks = health.data_checks()
        self.assertEqual(find(checks, 'Stock matches the batches')['status'], 'ok')
        self.assertEqual(health.section_score(checks), 100)

    def test_stock_that_differs_from_its_batches_is_found(self):
        self.buy(qty=5)                                              # 50 tablets
        Product.objects.filter(pk=self.product.pk).update(stock_qty=44)
        c = find(health.data_checks(), 'Stock does not match the batches')
        self.assertEqual((c['status'], c['lost']), ('bad', 10))
        self.assertTrue(any(i.startswith('Dolo 650: stock 44, batches 50') for i in c['items']))

    def test_negative_batch(self):
        self.buy(qty=5)
        Batch.objects.filter(product=self.product).update(qty=-3)
        self.assertEqual(find(health.data_checks(), 'Negative batch')['status'], 'bad')

    def test_stock_without_a_batch_record(self):
        Product.objects.filter(pk=self.product.pk).update(stock_qty=7)
        c = find(health.data_checks(), 'Stock without a batch record')
        self.assertEqual((c['status'], c['items']), ('warn', ['Dolo 650']))


@override_settings(**SAFE)
class ShopCheckTests(StockMixin, TestCase):
    def test_expired_stock_is_found(self):
        self.buy(qty=5)
        Batch.objects.filter(product=self.product).update(expire_date=today() - timedelta(days=3))
        c = find(health.business_checks(), 'Expired stock on the shelf')
        self.assertEqual((c['status'], c['lost']), ('warn', 5))
        self.assertTrue(c['items'][0].startswith('Dolo 650'))

    def test_expiring_soon_and_missing_mrp(self):
        self.buy(qty=5, days=10)
        checks = health.business_checks()
        self.assertEqual(find(checks, 'Expiring soon')['status'], 'info')
        self.assertEqual(find(checks, 'Medicines without an MRP')['status'], 'warn')
        Product.objects.filter(pk=self.product.pk).update(mrp=1, hsn_code='3004')
        checks = health.business_checks()
        self.assertEqual(find(checks, 'Every stocked medicine has an MRP')['status'], 'ok')
        self.assertEqual(find(checks, 'Every medicine has an HSN')['status'], 'ok')


@override_settings(**SAFE)
class ComputeAndPageTests(StockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        self.buy(qty=5)

    def test_compute_gives_a_whole_score_with_four_sections(self):
        data = health.compute()
        self.assertIsInstance(data['score'], int)
        self.assertTrue(0 <= data['score'] <= 100)
        self.assertEqual([s['key'] for s in data['sections']], ['data', 'safety', 'speed', 'business'])
        self.assertTrue(all(c['status'] in ('ok', 'warn', 'bad', 'info', 'unknown') for s in data['sections'] for c in s['checks']))
        self.assertEqual(find(next(s for s in data['sections'] if s['key'] == 'speed')['checks'], 'Product list')['status'], 'ok')

    def test_a_section_that_crashes_is_left_out_not_counted_as_good(self):
        with mock.patch.object(health, 'speed_checks', side_effect=RuntimeError('boom')):
            data = health.compute()
        speed = next(s for s in data['sections'] if s['key'] == 'speed')
        self.assertIsNone(speed['score'])
        self.assertGreaterEqual(data['unchecked'], 1)
        self.assertTrue(0 <= data['score'] <= 100)

    def test_one_failing_check_does_not_stop_the_others(self):
        with mock.patch('backend.models.Batch.objects') as bad:
            bad.exclude.side_effect = RuntimeError('boom')
            bad.filter.side_effect = RuntimeError('boom')
            checks = health.data_checks()
        self.assertTrue(any(c['status'] == 'unknown' for c in checks))
        self.assertTrue(any(c['status'] != 'unknown' for c in checks))

    def test_refresh_remembers_and_current_reuses_it(self):
        health.refresh()
        self.assertEqual(HealthSnapshot.objects.count(), 1)
        health.current(max_age_minutes=60)
        self.assertEqual(HealthSnapshot.objects.count(), 1)
        HealthSnapshot.objects.update(created_at=timezone.now() - timedelta(hours=3))
        health.current(max_age_minutes=60)
        self.assertEqual(HealthSnapshot.objects.count(), 2)

    def test_only_the_latest_snapshots_are_kept(self):
        for _ in range(health.HISTORY_KEEP + 3):
            HealthSnapshot.objects.create(score=50, data={})
        health.refresh()
        self.assertEqual(HealthSnapshot.objects.count(), health.HISTORY_KEEP)

    def test_the_page_opens_and_shows_the_reasons(self):
        resp = self.client.get(reverse('health'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Health Score')
        self.assertContains(resp, 'How the score is worked out')
        self.assertContains(resp, 'Stock matches the batches')

    def test_measure_again_needs_post_and_adds_a_snapshot(self):
        before = HealthSnapshot.objects.count()
        resp = self.client.post(reverse('health'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(HealthSnapshot.objects.count(), before + 1)

    def test_the_page_survives_a_failure(self):
        with mock.patch.object(health, 'current', side_effect=RuntimeError('boom')):
            resp = self.client.get(reverse('health'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'could not be measured')

    def test_only_the_owner_can_open_it(self):
        clerk = get_user_model().objects.create_user('clerk', password='pw-12345')
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(reverse('health')).status_code, 403)
        self.assertEqual(self.client.post(reverse('health')).status_code, 403)

    def test_the_admin_home_shows_the_score(self):
        resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="ms-health')
        self.assertContains(resp, 'Health score')

    def test_the_admin_home_opens_even_if_the_health_check_fails(self):
        with mock.patch.object(health, 'current', side_effect=RuntimeError('boom')):
            resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="ms-health')
