from django.core.management.base import BaseCommand

from backend.notify import alert_digest_text, collect_alerts, send_alert_digest
from backend.models import ShopProfile


class Command(BaseCommand):
    help = ("Low stock / expiry alerts ka digest email karta hai. "
            "Roz subah chalane ke liye cron/Task Scheduler me lagayein: python manage.py send_alerts")

    def add_arguments(self, parser):
        parser.add_argument('--to', nargs='*', help="Email addresses (default: DJANGO_ALERT_EMAILS ya Shop Settings ka email)")
        parser.add_argument('--print', action='store_true', dest='print_only', help="Email na bhejein, sirf screen par dikhayein")

    def handle(self, *args, **options):
        if options['print_only']:
            self.stdout.write(alert_digest_text(collect_alerts(), ShopProfile.get().name))
            return
        sent, recipients = send_alert_digest(options.get('to') or None)
        if not recipients:
            self.stdout.write(self.style.WARNING(
                "Koi email address nahi mila. DJANGO_ALERT_EMAILS set karein ya Shop Settings me email daalein."))
            return
        self.stdout.write(self.style.SUCCESS(f"Alert digest bhej diya: {', '.join(recipients)}"))
