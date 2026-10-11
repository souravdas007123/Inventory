from django.core.management.base import BaseCommand

from backend.notify import alert_digest_text, collect_alerts, send_alert_digest
from backend.models import ShopProfile


class Command(BaseCommand):
    help = ("Emails a digest of low stock / expiry alerts. "
            "To run it every morning, schedule it in cron / Task Scheduler: python manage.py send_alerts")

    def add_arguments(self, parser):
        parser.add_argument('--to', nargs='*', help="Email addresses (default: DJANGO_ALERT_EMAILS or the Shop Settings email)")
        parser.add_argument('--print', action='store_true', dest='print_only', help="Do not send the email; only print it on screen")

    def handle(self, *args, **options):
        if options['print_only']:
            self.stdout.write(alert_digest_text(collect_alerts(), ShopProfile.get().name))
            return
        sent, recipients = send_alert_digest(options.get('to') or None)
        if not recipients:
            self.stdout.write(self.style.WARNING(
                "No email address found. Set DJANGO_ALERT_EMAILS or add an email in Shop Settings."))
            return
        self.stdout.write(self.style.SUCCESS(f"Alert digest sent: {', '.join(recipients)}"))
