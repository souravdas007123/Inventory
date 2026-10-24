from django.core.management.base import BaseCommand
from django.db import connection

from tenants import reminders


class Command(BaseCommand):
    help = ("E-mails shop owners whose free trial or paid plan ends within 7 days, within 2 days, or has just ended. "
            "Each reminder is sent once. Normally run by `shops_daily`; use --dry-run to see who would get one.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only list who would get a reminder')

    def handle(self, *args, **opts):
        connection.set_schema_to_public()
        out = reminders.send_due(dry_run=opts['dry_run'])
        verb = 'would be sent' if opts['dry_run'] else 'sent'
        self.stdout.write(f"{out['checked']} shop(s) checked, {len(out['sent'])} reminder(s) {verb}"
                          + (f": {', '.join(out['sent'])}" if out['sent'] else '') + '.')
        if out['failed']:
            self.stderr.write(self.style.ERROR(f"Could not send to: {', '.join(out['failed'])}"))
