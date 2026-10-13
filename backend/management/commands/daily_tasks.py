from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from backend import backup, notify, recurring, summary
from backend.models import ShopProfile


class Command(BaseCommand):
    help = ("One command for the nightly scheduled task: backs up the database, then emails the daily summary "
            "(sales, credit, purchases, stock alerts). On PythonAnywhere: Tasks tab -> daily -> "
            "~/.virtualenvs/inv/bin/python ~/Inventory/manage.py daily_tasks")

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=14, help='How many latest backups to keep (default 14)')
        parser.add_argument('--skip-backup', action='store_true', help='Do not take a backup')
        parser.add_argument('--skip-email', action='store_true', help='Do not email the summary')
        parser.add_argument('--print', action='store_true', dest='print_only', help='Print the summary instead of emailing it')

    def handle(self, *args, **opts):
        if not opts['skip_backup']:
            try:
                self.stdout.write(self.style.SUCCESS(f"Backup created: {backup.make_backup(keep=opts['keep'])}"))
            except backup.BackupError as e:      # keep going: the summary is still useful
                self.stderr.write(self.style.ERROR(f"Backup failed: {e}"))
        try:
            made = recurring.post_due()
            if made:
                self.stdout.write(self.style.SUCCESS(f"{made} fixed expense / salary entr{'y' if made == 1 else 'ies'} added."))
        except Exception as e:       # must not stop the summary
            self.stderr.write(self.style.ERROR(f"Recurring expenses failed: {e}"))
        if opts['skip_email'] and not opts['print_only']:
            return
        shop = ShopProfile.get()
        text = summary.current_text(timezone.localdate())
        if opts['print_only']:
            self.stdout.write(text)
            return
        recipients = notify.alert_recipients()
        if not recipients:
            self.stdout.write(self.style.WARNING(
                "No email address found. Set DJANGO_ALERT_EMAILS or add an email in Shop Settings."))
            return
        try:
            send_mail(f"[{shop.name}] Daily summary {timezone.localdate():%d %b %Y}", text,
                      settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
        except Exception as e:                   # SMTP/network problem must not hide the backup result
            self.stderr.write(self.style.ERROR(f"Summary email failed: {e}"))
            return
        self.stdout.write(self.style.SUCCESS(f"Daily summary sent: {', '.join(recipients)}"))
