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
