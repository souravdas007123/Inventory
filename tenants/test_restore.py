"""Restoring a shop from a backup: the request, the platform page, the "please wait" page, and the order of the steps.

Needs the tenants app (MULTI_SHOP=1). The sandbox has no PostgreSQL, so the few PostgreSQL calls (rename / drop schema, pg_restore ...)
are replaced by a small fake that keeps a list of schemas. These tests prove the ORDER and the ROLLBACK logic: that the live data is only
renamed, that every failure puts it back, and that nothing is claimed or kept by mistake. The real pg_restore and ALTER SCHEMA
must still be tried once on PostgreSQL (see README, "Restore a shop")."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, modify_settings, override_settings
from django.utils import timezone

from backend import backup, tenancy

HAS_TENANTS = 'tenants' in settings.INSTALLED_APPS
if HAS_TENANTS:
    from tenants import restore, restoreviews
    from tenants.models import RestoreRequest, Shop
    from tenants.test_pay import HOST, PinTenantMiddleware
else:                                       # the single-shop setup: the tests below are skipped, the file must still load
    HOST, PinTenantMiddleware = {}, None

SCHEMA = 's_restore_test'
BACKUP = 'medsync_s_restore_test_20261101_020000.pgdump'


class World:
    """Stands in for PostgreSQL: a dict {schema: what it holds}. Every call is written to `calls`."""

    def __init__(self, shop_schema=SCHEMA):
        self.schema = shop_schema
        self.data = {shop_schema: 'ORIGINAL'}
        self.calls = []
        self.fail = set()               # names of steps that must fail: 'safety', 'load', 'verify', 'migrate', 'rename_back'
        self.problems = []

    def exists(self, name):
        return name in self.data

    def rename(self, old, new):
        self.calls.append(('rename', old, new))
        if old not in self.data or new in self.data:
            raise RuntimeError('rename impossible')
        if 'rename_back' in self.fail and new == self.schema:
            raise RuntimeError('rename back failed')
        self.data[new] = self.data.pop(old)

    def drop(self, name):
        self.calls.append(('drop', name))
        self.data.pop(name, None)

    def load(self, path):
        self.calls.append(('load', str(path)))
        if 'load' in self.fail:
            raise restore.RestoreError('pg_restore failed: boom')
        self.data[self.schema] = 'RESTORED'

    def verify(self, schema):
        self.calls.append(('verify', schema))
        return ['the table backend_bill is missing'] if 'verify' in self.fail else list(self.problems)

    def migrate(self, schema):
        self.calls.append(('migrate', schema))
        if 'migrate' in self.fail:
            raise RuntimeError('migration broke')

    def counts(self, schema):
        return {'products': 120 if self.data.get(schema) == 'RESTORED' else 90, 'bills': 300}

    def swap(self, live, other, parked):
        self.calls.append(('swap', live, other, parked))
        self.data[parked] = self.data.pop(live)
        self.data[live] = self.data.pop(other)

    def make_backup(self, keep=7):
        self.calls.append(('safety_backup',))
        if 'safety' in self.fail:
            raise backup.BackupError('disk full')
        return Path('medsync_s_restore_test_20261103_090000.pgdump')

    def patches(self):
        return [mock.patch.object(restore, 'schema_exists', self.exists), mock.patch.object(restore, 'rename_schema', self.rename),
                mock.patch.object(restore, 'drop_schema', self.drop), mock.patch.object(restore, 'load_dump', self.load),
                mock.patch.object(restore, 'verify', self.verify), mock.patch.object(restore, 'migrate', self.migrate),
                mock.patch.object(restore, 'counts', self.counts), mock.patch.object(restore, 'swap_schemas', self.swap),
                mock.patch.object(restore, 'check_ready', lambda shop, name, claimed=False: Path('x.pgdump')),
                mock.patch.object(backup, 'make_backup', self.make_backup),
                mock.patch.object(restore.time, 'sleep', lambda s: None),
                mock.patch.object(connection, 'set_schema_to_public', create=True)]


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**HOST)
@modify_settings(MIDDLEWARE={'append': 'tenants.test_pay.PinTenantMiddleware'})
class RestoreBase(TestCase):
    def setUp(self):
        cache.clear()
        self.shop = Shop(schema_name=SCHEMA, name='Sharma Medical', email='raj@example.com', plan='monthly',
                         paid_until=timezone.localdate() + timedelta(days=20), email_verified=True)
        self.shop.auto_create_schema = False
        self.shop.save()
        self.world = World()
        for p in self.world.patches():
            p.start()
            self.addCleanup(p.stop)
        self.req = RestoreRequest.objects.create(shop=self.shop, backup_name=BACKUP, requested_by='raj')

    def running(self):
        Shop.objects.filter(pk=self.shop.pk).update(restoring=True)
        RestoreRequest.objects.filter(pk=self.req.pk).update(status='running', started_at=timezone.now())

    def reload(self):
        self.req.refresh_from_db()
        self.shop.refresh_from_db()


class StepsTests(RestoreBase):
    def test_a_good_restore_renames_the_old_data_and_keeps_it(self):
        self.running()
        self.assertTrue(restore.execute(self.req.pk))
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('done', False))
        self.assertEqual(self.world.data[SCHEMA], 'RESTORED')
        self.assertEqual(self.world.data[self.req.old_schema], 'ORIGINAL')            # the old data is still there, only renamed
        self.assertTrue(self.req.old_schema.startswith('bak_') and self.req.old_schema.endswith('_' + SCHEMA))
        self.assertEqual(self.req.safety_backup, 'medsync_s_restore_test_20261103_090000.pgdump')
        self.assertIn('120 products', self.req.message)
        self.assertIn('Before: 90 products', self.req.message)
        self.assertNotIn('drop', [c[0] for c in self.world.calls])                    # nothing was deleted

    def test_the_shops_audit_log_gets_a_line(self):
        self.running()
        with mock.patch('backend.auditlog.record') as rec:
            restore.execute(self.req.pk)
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][1:3], ('BACKUP', 'Restore'))
        self.assertIn(BACKUP, rec.call_args[0][3])

    def test_a_note_that_cannot_be_written_does_not_undo_a_good_restore(self):
        self.running()
        with mock.patch('backend.auditlog.record', side_effect=RuntimeError('db')):
            self.assertTrue(restore.execute(self.req.pk))
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('done', False))

    def test_the_order_is_safety_backup_then_rename_then_load_then_check_then_migrate(self):
        self.running()
        restore.execute(self.req.pk)
        steps = [c[0] for c in self.world.calls]
        self.assertEqual(steps, ['safety_backup', 'rename', 'load', 'verify', 'migrate'])

    def test_the_saved_copy_name_is_written_before_the_rename(self):
        self.running()
        seen = {}
        real = self.world.rename

        def spy(old, new):
            seen['old_schema'] = RestoreRequest.objects.get(pk=self.req.pk).old_schema
            real(old, new)
        with mock.patch.object(restore, 'rename_schema', spy):
            restore.execute(self.req.pk)
        self.assertTrue(seen['old_schema'].startswith('bak_'))                        # a crash right after the rename can be undone

    def failing(self, step):
        self.world.fail.add(step)
        self.running()
        self.assertFalse(restore.execute(self.req.pk))
        self.reload()

    def assert_back_as_before(self):
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})                       # exactly one schema, the original, under its name
        self.assertEqual((self.req.status, self.shop.restoring, self.req.old_schema), ('failed', False, ''))
        self.assertIn('exactly as it was', self.req.message)

    def test_pg_restore_failing_puts_everything_back(self):
        self.failing('load')
        self.assert_back_as_before()
        self.assertIn('pg_restore failed', self.req.message)

    def test_a_restored_schema_that_fails_the_check_is_dropped_and_the_old_one_comes_back(self):
        self.failing('verify')
        self.assert_back_as_before()
        self.assertIn('did not pass the check', self.req.message)
        self.assertIn(('drop', SCHEMA), self.world.calls)

    def test_a_failing_migration_puts_everything_back(self):
        self.failing('migrate')
        self.assert_back_as_before()

    def test_no_safety_backup_means_nothing_is_touched(self):
        self.failing('safety')
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})
        self.assertNotIn('rename', [c[0] for c in self.world.calls])
        self.assertEqual((self.req.status, self.shop.restoring), ('failed', False))

    def test_an_unexpected_error_is_also_rolled_back(self):
        self.running()
        with mock.patch.object(restore, 'load_dump', side_effect=ValueError('weird')):
            self.assertFalse(restore.execute(self.req.pk))
        self.reload()
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})
        self.assertIn('Unexpected problem', self.req.message)

    def test_when_the_rollback_itself_fails_the_shop_stays_on_the_wait_page_and_recover_fixes_it(self):
        self.world.fail.add('load')
        self.world.fail.add('rename_back')
        self.running()
        self.assertFalse(restore.execute(self.req.pk))
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('failed', True))   # not served: a broken shop is never shown
        self.assertIn('NOT put back automatically', self.req.message)
        self.assertTrue(self.req.old_schema)
        self.world.fail.discard('rename_back')
        restore.recover(self.req.pk)
        self.reload()
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})
        self.assertEqual((self.req.status, self.shop.restoring, self.req.old_schema), ('failed', False, ''))

    def test_recover_does_nothing_for_a_shop_that_is_not_waiting(self):
        with self.assertRaises(restore.RestoreError):
            restore.recover(self.req.pk)                                          # still only requested

    def test_recover_after_a_crash_between_rename_and_load(self):
        self.running()
        RestoreRequest.objects.filter(pk=self.req.pk).update(old_schema='bak_20261103090000_' + SCHEMA)
        self.world.data = {'bak_20261103090000_' + SCHEMA: 'ORIGINAL'}               # the process died here: no live schema
        restore.recover(self.req.pk)
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})
        self.reload()
        self.assertFalse(self.shop.restoring)

    def test_recover_after_a_crash_before_the_rename(self):
        self.running()
        RestoreRequest.objects.filter(pk=self.req.pk).update(old_schema='bak_20261103090000_' + SCHEMA)   # saved, but never renamed
        restore.recover(self.req.pk)
        self.assertEqual(self.world.data, {SCHEMA: 'ORIGINAL'})                    # the live data was never touched
        self.reload()
        self.assertEqual((self.shop.restoring, self.req.status), (False, 'failed'))

    def test_a_request_that_was_not_started_is_not_run(self):
        with self.assertRaises(restore.RestoreError):
            restore.execute(self.req.pk)
        self.assertEqual(self.world.calls, [])


class UndoAndCleanupTests(RestoreBase):
    def done(self):
        self.running()
        restore.execute(self.req.pk)
        self.reload()

    def test_undo_swaps_back_in_one_step_and_keeps_the_restored_data(self):
        self.done()
        old = self.req.old_schema
        restore.undo(self.req.pk)
        self.reload()
        self.assertEqual(self.world.data[SCHEMA], 'ORIGINAL')
        self.assertEqual(self.world.data[self.req.old_schema], 'RESTORED')           # nothing is lost
        self.assertEqual((self.req.status, self.shop.restoring), ('undone', False))
        self.assertNotEqual(self.req.old_schema, old)
        with self.assertRaises(restore.RestoreError):
            restore.undo(self.req.pk)                                              # only once

    def test_a_failed_undo_leaves_the_shop_as_it_is(self):
        self.done()
        with mock.patch.object(restore, 'swap_schemas', side_effect=RuntimeError('x')):
            with self.assertRaises(restore.RestoreError):
                restore.undo(self.req.pk)
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('done', False))
        self.assertEqual(self.world.data[SCHEMA], 'RESTORED')

    def test_undo_is_refused_when_the_old_data_is_gone(self):
        self.done()
        self.world.data.pop(self.req.old_schema)
        with self.assertRaises(restore.RestoreError):
            restore.undo(self.req.pk)

    def test_undo_is_refused_while_the_shop_is_being_restored(self):
        self.done()
        Shop.objects.filter(pk=self.shop.pk).update(restoring=True)
        with self.assertRaises(restore.RestoreError):
            restore.undo(self.req.pk)

    def test_cleanup_drops_only_old_saved_copies_that_a_restore_made(self):
        self.done()
        name = self.req.old_schema
        self.assertEqual(restore.cleanup(), [])                                    # too new
        self.assertIn(name, self.world.data)
        RestoreRequest.objects.filter(pk=self.req.pk).update(finished_at=timezone.now() - timedelta(days=8))
        self.assertEqual(restore.cleanup(), [name])
        self.assertNotIn(name, self.world.data)
        self.assertEqual(self.world.data[SCHEMA], 'RESTORED')                      # the live shop is never touched
        self.assertEqual(restore.cleanup(), [])                                    # and it only happens once
        self.assertEqual(RestoreRequest.objects.get(pk=self.req.pk).old_schema, '')

    def test_cleanup_never_drops_a_name_that_does_not_look_like_a_saved_copy(self):
        self.done()
        RestoreRequest.objects.filter(pk=self.req.pk).update(finished_at=timezone.now() - timedelta(days=30), old_schema='public')
        self.assertEqual(restore.cleanup(), [])
        self.assertNotIn(('drop', 'public'), self.world.calls)

    def test_can_undo_window(self):
        self.done()
        self.assertTrue(restore.can_undo(self.req))
        self.req.finished_at = timezone.now() - timedelta(days=restore.KEEP_DAYS + 1)
        self.assertFalse(restore.can_undo(self.req))


class StartTests(RestoreBase):
    def test_start_claims_the_request_and_the_shop_and_launches_the_command(self):
        with mock.patch.object(restore.subprocess, 'Popen') as popen:
            req = restore.start(self.req.pk)
        self.assertEqual(req.status, 'running')
        self.shop.refresh_from_db()
        self.assertTrue(self.shop.restoring)
        args = popen.call_args[0][0]
        self.assertEqual(args[-2:], ['run_restore', str(self.req.pk)])
        self.assertTrue(popen.call_args[1]['start_new_session'])                      # it keeps running after the web request ends

    def test_a_second_start_is_refused(self):
        with mock.patch.object(restore.subprocess, 'Popen'):
            restore.start(self.req.pk)
            with self.assertRaises(restore.RestoreError):
                restore.start(self.req.pk)

    def test_a_shop_that_is_already_restoring_is_not_claimed_twice(self):
        Shop.objects.filter(pk=self.shop.pk).update(restoring=True)
        with mock.patch.object(restore.subprocess, 'Popen') as popen:
            with self.assertRaises(restore.RestoreError):
                restore.start(self.req.pk)
        popen.assert_not_called()
        self.assertEqual(RestoreRequest.objects.get(pk=self.req.pk).status, 'requested')

    def test_a_failed_check_changes_nothing(self):
        with mock.patch.object(restore, 'check_ready', side_effect=restore.RestoreError('not enough disk')):
            with self.assertRaises(restore.RestoreError):
                restore.start(self.req.pk)
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('requested', False))

    def test_a_command_that_cannot_launch_frees_the_shop(self):
        with mock.patch.object(restore.subprocess, 'Popen', side_effect=OSError('no python')):
            with self.assertRaises(restore.RestoreError):
                restore.start(self.req.pk)
        self.reload()
        self.assertEqual((self.req.status, self.shop.restoring), ('failed', False))

    def test_only_a_waiting_request_can_start(self):
        for status in ('done', 'failed', 'rejected', 'cancelled', 'running'):
            RestoreRequest.objects.filter(pk=self.req.pk).update(status=status)
            with self.assertRaises(restore.RestoreError):
                restore.start(self.req.pk)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class CheckReadyTests(SimpleTestCase):
    """The real checks, on real files, with only PostgreSQL's own tools and the database replaced."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / BACKUP
        self.file.write_bytes(b'PGDMP' + b'x' * 100)
        self.shop = Shop(schema_name=SCHEMA, name='S')
        stack = [override_settings(BACKUP_DIR=str(self.dir), MULTI_SHOP=True), mock.patch.object(backup, 'is_postgres', return_value=True),
                 mock.patch.object(backup, 'find_pg_restore', return_value='/usr/bin/pg_restore'),
                 mock.patch.object(restore, 'schema_exists', return_value=True),
                 mock.patch.object(restore, 'dump_schemas', return_value={SCHEMA}),
                 mock.patch.object(restore.shutil, 'disk_usage', return_value=mock.Mock(free=10 * 1024 ** 3))]
        for s in stack:
            s.enable() if hasattr(s, 'enable') else s.start()
            self.addCleanup(s.disable if hasattr(s, 'disable') else s.stop)

    def test_a_good_file_passes(self):
        self.assertEqual(restore.check_ready(self.shop, BACKUP), self.file)

    def test_each_problem_is_told_plainly(self):
        cases = []
        with mock.patch.object(backup, 'is_postgres', return_value=False):
            cases.append(('PostgreSQL', BACKUP))
            with self.assertRaisesRegex(restore.RestoreError, 'PostgreSQL'):
                restore.check_ready(self.shop, BACKUP)
        with self.assertRaisesRegex(restore.RestoreError, 'not found'):
            restore.check_ready(self.shop, 'medsync_s_restore_test_20200101_000000.pgdump')
        for odd in ('../../etc/passwd', 'medsync_20261101_020000.pgdump', 'medsync_s_other_20261101_020000.pgdump', ''):
            with self.assertRaisesRegex(restore.RestoreError, 'not found'):
                restore.check_ready(self.shop, odd)                                  # another shop's file or a path trick
        self.file.write_bytes(b'NOTPG' + b'x' * 100)
        with self.assertRaisesRegex(restore.RestoreError, 'not a PostgreSQL backup'):
            restore.check_ready(self.shop, BACKUP)
        self.file.write_bytes(b'PGDMP' + b'x' * 100)
        with mock.patch.object(backup, 'find_pg_restore', return_value=''):
            with self.assertRaisesRegex(restore.RestoreError, 'pg_restore was not found'):
                restore.check_ready(self.shop, BACKUP)
        with mock.patch.object(restore.shutil, 'disk_usage', return_value=mock.Mock(free=1024)):
            with self.assertRaisesRegex(restore.RestoreError, 'disk space'):
                restore.check_ready(self.shop, BACKUP)
        with mock.patch.object(restore, 'dump_schemas', return_value={'s_other_shop'}):
            with self.assertRaisesRegex(restore.RestoreError, 'does not belong'):
                restore.check_ready(self.shop, BACKUP)
        with mock.patch.object(restore, 'dump_schemas', return_value={SCHEMA, 'public'}):
            with self.assertRaisesRegex(restore.RestoreError, 'does not belong'):
                restore.check_ready(self.shop, BACKUP)                               # a file that also carries another schema
        self.shop.restoring = True
        with self.assertRaisesRegex(restore.RestoreError, 'already being restored'):
            restore.check_ready(self.shop, BACKUP)
        self.assertEqual(restore.check_ready(self.shop, BACKUP, claimed=True), self.file)
        public = Shop(schema_name='public', name='Main')
        with self.assertRaisesRegex(restore.RestoreError, 'main shop'):
            restore.check_ready(public, BACKUP)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class GuardTests(SimpleTestCase):
    def test_drop_refuses_names_a_restore_did_not_make(self):
        for bad in ('public', 'auth', 'pg_catalog', 'information_schema', '', 'bak_', 'bak_2026_x', 'x; DROP SCHEMA public'):
            with self.assertRaises(restore.RestoreError, msg=bad):
                restore.drop_schema(bad)

    def test_saved_copy_names_are_recognised_exactly(self):
        for good in ('bak_20261103090000_s_shop', 'bak_20261103090000_2_s_shop'):
            self.assertTrue(restore.OLD_RE.match(good), good)
        for bad in ('s_shop', 'public', 'bak_202611030900_s_shop', 'bak_20261103090000_S_shop', 'bak_20261103090000_s;shop'):
            self.assertFalse(restore.OLD_RE.match(bad), bad)

    def test_a_new_saved_copy_name_never_clashes_with_an_existing_one(self):
        taken = set()
        with mock.patch.object(restore, 'schema_exists', lambda n: n in taken):
            first = restore.parked_name(SCHEMA)
            taken.add(first)
            second = restore.parked_name(SCHEMA)
        self.assertNotEqual(first, second)
        self.assertTrue(restore.OLD_RE.match(first) and restore.OLD_RE.match(second))
        self.assertLessEqual(len(second), 63)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class BackupHelperTests(SimpleTestCase):
    def test_a_shops_backups_are_listed_by_its_schema_name_only(self):
        with TemporaryDirectory() as tmp, override_settings(BACKUP_DIR=tmp, MULTI_SHOP=True):
            for n in (BACKUP, 'medsync_s_other_20261101_020000.pgdump', 'medsync_20261101_020000.pgdump', 'medsync_s_restore_test_x_20261101_020000.pgdump'):
                (Path(tmp) / n).write_bytes(b'PGDMP')
            self.assertEqual([b['name'] for b in backup.list_backups(SCHEMA)], [BACKUP])
            self.assertEqual(backup.backup_path(BACKUP, SCHEMA), Path(tmp) / BACKUP)
            self.assertIsNone(backup.backup_path('medsync_s_other_20261101_020000.pgdump', SCHEMA))

    def test_pg_restore_is_found_next_to_pg_dump(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / 'pg_dump').write_bytes(b'')
            (Path(tmp) / 'pg_restore').write_bytes(b'')
            with mock.patch.object(backup, 'find_pg_dump', return_value=str(Path(tmp) / 'pg_dump')), \
                    mock.patch.object(backup.shutil, 'which', return_value=None), mock.patch.dict('os.environ', {'PG_RESTORE': ''}):
                self.assertEqual(backup.find_pg_restore(), str(Path(tmp) / 'pg_restore'))


class ShopSideTests(RestoreBase):
    def setUp(self):
        super().setUp()
        self.req.delete()
        self.owner = get_user_model().objects.create_superuser('raj', 'raj@example.com', 'pw-12345')
        self.client.force_login(self.owner)
        for p in (mock.patch.object(restoreviews, '_shop', return_value=self.shop), mock.patch.object(tenancy, 'on_shop_site', return_value=True),
                  mock.patch.object(backup, 'list_backups', return_value=[dict(name=BACKUP, size_kb=40, when=timezone.now())])):
            p.start()
            self.addCleanup(p.stop)
        PinTenantMiddleware.shop = self.shop
        self.addCleanup(setattr, PinTenantMiddleware, 'shop', None)
        mail.outbox.clear()

    def ask(self, **kw):
        data = dict(backup=BACKUP, note='I deleted all products', understand='1')
        data.update(kw)
        return self.client.post('/restore/request/', data)

    def test_a_request_is_saved_and_the_platform_owner_is_told(self):
        with override_settings(ALERT_EMAILS=['owner@example.com']):
            r = self.ask()
        self.assertRedirects(r, '/backup/', fetch_redirect_response=False)
        req = RestoreRequest.objects.get()
        self.assertEqual((req.shop, req.backup_name, req.status, req.requested_by, req.note), (self.shop, BACKUP, 'requested', 'raj', 'I deleted all products'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(BACKUP, mail.outbox[0].body)
        self.assertEqual(self.shop.restoring, False)                                # asking changes nothing about the data

    def test_the_request_needs_a_real_backup_the_box_and_only_one_at_a_time(self):
        for bad in (dict(backup='medsync_s_other_20261101_020000.pgdump'), dict(backup=''), dict(backup='../../x'), dict(understand='')):
            self.ask(**bad)
            self.assertEqual(RestoreRequest.objects.count(), 0, bad)
        self.ask()
        self.ask()
        self.assertEqual(RestoreRequest.objects.count(), 1)

    def test_a_long_note_is_cut_and_a_nul_character_never_reaches_the_database(self):
        self.ask(note='x' * 900 + '\x00')
        self.assertEqual(len(RestoreRequest.objects.get().note), restoreviews.NOTE_MAX)

    def test_only_a_post_and_only_the_owner(self):
        self.assertEqual(self.client.get('/restore/request/').status_code, 405)
        self.client.force_login(get_user_model().objects.create_user('clerk', 'c@example.com', 'pw-12345'))
        self.ask()
        self.assertEqual(RestoreRequest.objects.count(), 0)

    def test_cancel_works_only_while_waiting_and_only_for_this_shop(self):
        self.ask()
        req = RestoreRequest.objects.get()
        other = Shop(schema_name='s_other_shop', name='Other')
        other.auto_create_schema = False
        other.save()
        theirs = RestoreRequest.objects.create(shop=other, backup_name='medsync_s_other_shop_20261101_020000.pgdump')
        self.assertEqual(self.client.post(f'/restore/{theirs.pk}/cancel/').status_code, 404)
        self.client.post(f'/restore/{req.pk}/cancel/')
        req.refresh_from_db()
        self.assertEqual(req.status, 'cancelled')
        RestoreRequest.objects.filter(pk=req.pk).update(status='running')
        self.client.post(f'/restore/{req.pk}/cancel/')
        req.refresh_from_db()
        self.assertEqual(req.status, 'running')

    def test_the_backup_page_shows_the_form_and_the_requests(self):
        r = self.client.get('/backup/')
        self.assertContains(r, 'Restore from a backup')
        self.assertContains(r, 'Ask us to restore')
        self.assertContains(r, BACKUP)
        self.ask()
        r = self.client.get('/backup/')
        self.assertContains(r, 'You already have a restore request')
        self.assertNotContains(r, 'Ask us to restore')
        self.assertContains(r, 'Waiting')

    def test_a_decline_reason_is_visible_to_the_shop(self):
        RestoreRequest.objects.create(shop=self.shop, backup_name=BACKUP, status='rejected', message='Please call us first.')
        self.assertContains(self.client.get('/backup/'), 'Please call us first.')


class PlatformSideTests(RestoreBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345')
        self.client.force_login(self.admin)
        self.word = 'restore-test'
        self.patch_word = mock.patch.object(restoreviews, 'shop_name_to_type', return_value=self.word)
        self.patch_word.start()
        self.addCleanup(self.patch_word.stop)

    def post(self, action, **kw):
        return self.client.post(f'/superadmin/restores/{self.req.pk}/action/', dict(action=action, **kw))

    def test_the_page_lists_requests_and_the_menu_counts_them(self):
        r = self.client.get('/superadmin/restores/')
        self.assertContains(r, 'Sharma Medical')
        self.assertContains(r, BACKUP)
        self.assertContains(r, 'Restore now')
        self.assertContains(r, '1 restore request')

    def test_start_needs_the_shop_name_typed(self):
        with mock.patch.object(restore, 'start') as start:
            self.post('start', confirm='wrong')
            self.post('start')
            start.assert_not_called()
            self.post('start', confirm='  Restore-Test ')
            start.assert_called_once_with(self.req.pk)

    def test_a_start_that_cannot_go_ahead_shows_the_reason(self):
        with mock.patch.object(restore, 'start', side_effect=restore.RestoreError('There is not enough free disk space to restore safely.')):
            r = self.client.post(f'/superadmin/restores/{self.req.pk}/action/', dict(action='start', confirm=self.word), follow=True)
        self.assertContains(r, 'not enough free disk space')

    def test_decline_saves_the_reason_and_only_for_a_waiting_request(self):
        self.post('reject', reason='Please call us first.')
        self.reload()
        self.assertEqual((self.req.status, self.req.message), ('rejected', 'Please call us first.'))
        self.post('reject', reason='again')
        self.reload()
        self.assertEqual(self.req.message, 'Please call us first.')

    def test_undo_and_recover_buttons_appear_only_when_they_apply(self):
        self.running()
        restore.execute(self.req.pk)
        r = self.client.get('/superadmin/restores/')
        self.assertContains(r, 'Undo this restore')
        self.assertNotContains(r, 'Recover (put the shop back)')
        RestoreRequest.objects.filter(pk=self.req.pk).update(status='running', started_at=timezone.now() - timedelta(minutes=restore.STUCK_MINUTES + 5))
        r = self.client.get('/superadmin/restores/')
        self.assertContains(r, 'Recover (put the shop back)')
        self.assertContains(r, 'looks stuck')

    def test_undo_through_the_page(self):
        self.running()
        restore.execute(self.req.pk)
        self.post('undo', confirm='nope')
        self.assertEqual(self.world.data[SCHEMA], 'RESTORED')
        self.post('undo', confirm=self.word)
        self.assertEqual(self.world.data[SCHEMA], 'ORIGINAL')

    def test_others_cannot_see_or_use_the_pages(self):
        self.client.logout()
        self.assertEqual(self.client.get('/superadmin/restores/').status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('clerk', 'c@example.com', 'pw-12345'))
        self.assertEqual(self.client.get('/superadmin/restores/').status_code, 404)
        with mock.patch.object(restore, 'start') as start:
            self.assertEqual(self.post('start', confirm=self.word).status_code, 404)
            start.assert_not_called()

    def test_an_unknown_action_does_nothing(self):
        r = self.post('format-disk', confirm=self.word)
        self.assertEqual(r.status_code, 302)
        self.reload()
        self.assertEqual(self.req.status, 'requested')


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class WaitPageAndNightlyTests(RestoreBase):
    def test_a_shop_that_is_being_restored_shows_the_wait_page_not_its_data(self):
        from django.http import HttpResponse
        from tenants.middleware import ShopAccessMiddleware
        from django.test import RequestFactory
        Shop.objects.filter(pk=self.shop.pk).update(restoring=True)
        self.shop.refresh_from_db()
        called = []
        mw = ShopAccessMiddleware(lambda r: called.append(r) or HttpResponse('shop'))
        req = RequestFactory().get('/products/', HTTP_HOST='x.localhost')
        req.tenant = self.shop
        resp = mw(req)
        self.assertEqual(resp.status_code, 503)
        self.assertIn('being restored', resp.content.decode())
        self.assertEqual((resp['Retry-After'], resp['Cache-Control']), ('60', 'no-store'))
        self.assertEqual(called, [])                                                # the shop's own views never ran
        ok = RequestFactory().get('/healthz/', HTTP_HOST='x.localhost')
        ok.tenant = self.shop
        self.assertEqual(mw(ok).status_code, 200)                                   # the monitor still works
        Shop.objects.filter(pk=self.shop.pk).update(restoring=False)
        self.shop.refresh_from_db()
        again = RequestFactory().get('/products/', HTTP_HOST='x.localhost')
        again.tenant = self.shop
        self.assertEqual(mw(again).status_code, 200)

    def test_the_nightly_job_skips_a_shop_that_is_being_restored(self):
        Shop.objects.filter(pk=self.shop.pk).update(restoring=True)
        with mock.patch.object(backup, 'make_backup') as made, mock.patch.object(restore, 'cleanup', return_value=[]):
            from io import StringIO
            out = StringIO()
            try:
                call_command('shops_daily', '--skip-reminders', '--only', SCHEMA, stdout=out, stderr=StringIO())
            except CommandError:
                pass
        made.assert_not_called()
        self.assertIn('being restored', out.getvalue())

    def test_the_command_reports_a_failed_restore_as_an_error(self):
        with mock.patch.object(restore, 'execute', return_value=False):
            with self.assertRaises(CommandError):
                call_command('run_restore', '1')
        with self.assertRaises(CommandError):
            call_command('run_restore')
        with mock.patch.object(restore, 'cleanup', return_value=['bak_x']):
            from io import StringIO
            out = StringIO()
            call_command('run_restore', '--cleanup', stdout=out)
            self.assertIn('bak_x', out.getvalue())
