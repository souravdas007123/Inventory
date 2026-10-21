from django.core.management.base import BaseCommand, CommandError

from backend import backup


class Command(BaseCommand):
    help = "Creates a backup of the database (SQLite: compressed copy; PostgreSQL: pg_dump) (for cron / PythonAnywhere scheduled tasks)."

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=14, help='How many latest backups to keep (default 14)')

    def handle(self, *args, **opts):
        try:
            path = backup.make_backup(keep=opts['keep'])
        except backup.BackupError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f'Backup created: {path}'))
