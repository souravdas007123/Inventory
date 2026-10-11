from django.core.management.base import BaseCommand, CommandError

from backend import backup


class Command(BaseCommand):
    help = "SQLite database ka compressed backup banata hai (cron / PythonAnywhere scheduled task ke liye)."

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=14, help='Kitne latest backups rakhne hain (default 14)')

    def handle(self, *args, **opts):
        try:
            path = backup.make_backup(keep=opts['keep'])
        except backup.BackupError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f'Backup ban gaya: {path}'))
