"""Backups when the database is PostgreSQL (pg_dump is faked here, so no PostgreSQL is needed to run these tests)."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from backend import backup, health

PG = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'inventory_db', 'USER': 'u', 'PASSWORD': 'secret',
                  'HOST': 'localhost', 'PORT': '5432'}}


class PostgresBackupTests(SimpleTestCase):
    def run_backup(self, tmp, returncode=0, stderr='', write=True, exe='/usr/bin/pg_dump'):
        def fake_run(cmd, **kw):
            if write and returncode == 0:
                Path(cmd[cmd.index('-f') + 1]).write_bytes(b'PGDMP' + b'0' * 50)
            return SimpleNamespace(returncode=returncode, stderr=stderr)
        with override_settings(DATABASES=PG, BACKUP_DIR=tmp), \
                mock.patch.object(backup, 'find_pg_dump', return_value=exe), \
                mock.patch.object(backup.subprocess, 'run', side_effect=fake_run) as run:
            return backup.make_backup(), run

    def test_a_pgdump_file_is_made_and_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, run = self.run_backup(tmp)
            self.assertTrue(path.name.endswith('.pgdump'))
            self.assertTrue(backup.NAME_RE.match(path.name))
            cmd, kw = run.call_args.args[0], run.call_args.kwargs
            self.assertNotIn('secret', ' '.join(cmd))                       # the password is not on the command line
            self.assertEqual(kw['env']['PGPASSWORD'], 'secret')
            with override_settings(BACKUP_DIR=tmp):
                self.assertEqual([b['name'] for b in backup.list_backups()], [path.name])
                self.assertIsNotNone(backup.backup_path(path.name))

    def test_a_failed_pg_dump_gives_a_message_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(backup.BackupError) as ctx:
                self.run_backup(tmp, returncode=1, stderr='password authentication failed')
            self.assertIn('password authentication failed', str(ctx.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_pg_dump_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(backup.BackupError) as ctx:
                self.run_backup(tmp, exe='')
            self.assertIn('PG_DUMP', str(ctx.exception))

    def test_an_empty_dump_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(DATABASES=PG, BACKUP_DIR=tmp), \
                    mock.patch.object(backup, 'find_pg_dump', return_value='pg_dump'), \
                    mock.patch.object(backup.subprocess, 'run',
                                      side_effect=lambda cmd, **kw: (Path(cmd[cmd.index('-f') + 1]).write_bytes(b''), SimpleNamespace(returncode=0, stderr=''))[1]):
                with self.assertRaises(backup.BackupError):
                    backup.make_backup()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_health_check_of_a_pgdump_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / 'medsync_20261016_010101.pgdump', Path(tmp) / 'medsync_20261016_020202.pgdump'
            good.write_bytes(b'PGDMP12345')
            bad.write_bytes(b'not a backup')
            self.assertEqual(health._verify_backup(good), '')
            self.assertNotEqual(health._verify_backup(bad), '')

    def test_other_names_are_still_refused(self):
        for name in ('../medsync_20261016_010101.pgdump', 'medsync_20261016_010101.pgdump.exe', 'x.pgdump'):
            self.assertFalse(backup.NAME_RE.match(name), name)


class ShopBackupScopeTests(SimpleTestCase):
    """With many shops, a shop's backup holds only its own tables and is invisible to every other shop."""

    def make(self, tmp, name):
        (Path(tmp) / name).write_bytes(b'PGDMP' + b'0' * 20)

    def test_a_shop_sees_only_its_own_backups_and_the_platform_only_its_own(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(BACKUP_DIR=tmp):
            for name in ('medsync_20261016_010101.pgdump', 'medsync_s_demo_20261016_010101.pgdump',
                         'medsync_s_other_20261016_010101.pgdump'):
                self.make(tmp, name)
            with mock.patch.object(backup, 'current_schema', return_value='s_demo'):
                self.assertEqual([b['name'] for b in backup.list_backups()], ['medsync_s_demo_20261016_010101.pgdump'])
                self.assertIsNone(backup.backup_path('medsync_20261016_010101.pgdump'))            # the platform's file
                self.assertIsNone(backup.backup_path('medsync_s_other_20261016_010101.pgdump'))    # another shop's file
                self.assertIsNotNone(backup.backup_path('medsync_s_demo_20261016_010101.pgdump'))
            with mock.patch.object(backup, 'current_schema', return_value=''):
                self.assertEqual([b['name'] for b in backup.list_backups()], ['medsync_20261016_010101.pgdump'])
                self.assertIsNone(backup.backup_path('medsync_s_demo_20261016_010101.pgdump'))

    def test_a_shop_backup_dumps_only_its_own_schema(self):
        def fake_run(cmd, **kw):
            Path(cmd[cmd.index('-f') + 1]).write_bytes(b'PGDMP' + b'0' * 50)
            return SimpleNamespace(returncode=0, stderr='')
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(DATABASES=PG, BACKUP_DIR=tmp), \
                    mock.patch.object(backup, 'find_pg_dump', return_value='pg_dump'), \
                    mock.patch.object(backup, 'current_schema', return_value='s_demo'), \
                    mock.patch.object(backup.subprocess, 'run', side_effect=fake_run) as run:
                path = backup.make_backup()
            cmd = run.call_args.args[0]
            self.assertIn('-n', cmd)
            self.assertEqual(cmd[cmd.index('-n') + 1], 's_demo')
            self.assertEqual(cmd[-1], PG['default']['NAME'])             # the database name stays the last argument
            self.assertTrue(path.name.startswith('medsync_s_demo_'))

    def test_the_platform_backup_has_no_schema_filter(self):
        def fake_run(cmd, **kw):
            Path(cmd[cmd.index('-f') + 1]).write_bytes(b'PGDMP' + b'0' * 50)
            return SimpleNamespace(returncode=0, stderr='')
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(DATABASES=PG, BACKUP_DIR=tmp), \
                    mock.patch.object(backup, 'find_pg_dump', return_value='pg_dump'), \
                    mock.patch.object(backup, 'current_schema', return_value=''), \
                    mock.patch.object(backup.subprocess, 'run', side_effect=fake_run) as run:
                backup.make_backup()
            self.assertNotIn('-n', run.call_args.args[0])

    def test_current_schema_is_empty_when_many_shops_is_off(self):
        with override_settings(MULTI_SHOP=False):
            self.assertEqual(backup.current_schema(), '')


class RetentionTests(SimpleTestCase):
    """Only the latest 7 backups of a shop are kept; the older ones go, and one shop never removes another shop's files."""

    def names(self, schema, n):
        stem = f'medsync_{schema}_' if schema else 'medsync_'
        return [f'{stem}202610{d:02d}_020000.pgdump' for d in range(1, n + 1)]

    def test_the_default_is_seven(self):
        import inspect
        self.assertEqual(backup.KEEP, 7)
        self.assertEqual(inspect.signature(backup.make_backup).parameters['keep'].default, 7)

    def test_a_new_backup_removes_the_oldest_beyond_seven_of_that_shop_only(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(BACKUP_DIR=tmp, MULTI_SHOP=True):
            for n in self.names('s_a', 10) + self.names('s_b', 10) + self.names('', 10):
                (Path(tmp) / n).write_bytes(b'PGDMP')
            with mock.patch.object(backup, 'current_schema', return_value='s_a'):
                backup.prune(backup.KEEP)
                self.assertEqual(len(backup.list_backups()), 7)
                self.assertEqual(backup.list_backups()[-1]['name'], 's_a'.join(['medsync_', '_20261004_020000.pgdump']))    # the 3 oldest are gone
            left = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(sum(n.startswith('medsync_s_a_') for n in left), 7)
            self.assertEqual(sum(n.startswith('medsync_s_b_') for n in left), 10)       # another shop's files are untouched
            self.assertEqual(sum(n.startswith('medsync_2026') for n in left), 10)       # and so are the main shop's

    def test_the_commands_and_the_page_use_the_same_number(self):
        from django.core.management import get_commands, load_command_class
        for name in ('backup_db', 'daily_tasks'):
            cmd = load_command_class(get_commands()[name], name)
            parser = cmd.create_parser('manage.py', name)
            self.assertEqual(parser.parse_args([]).keep, 7)
