"""The nightly job for a many-shop install: a backup of every shop, each shop's daily tasks and summary e-mail,
and the plan reminders. One cron line for everything. Each shop is handled on its own, so one failure never stops the rest."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from backend import backup, tenancy
from tenants import reminders
from tenants.models import Shop


class Command(BaseCommand):
    help = ("Nightly job for all shops: backup of every shop (a shop's backup holds only its own data; the main shop's "
            "backup is the whole database), daily tasks and summary e-mail for shops on a trial or a paid plan, and "
            "reminders for plans that are ending. Example cron: 30 2 * * * <venv>/bin/python manage.py shops_daily")

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=14, help='How many latest backups to keep for each shop (default 14)')
        parser.add_argument('--skip-backup', action='store_true', help='Do not take backups')
        parser.add_argument('--skip-email', action='store_true', help="Do not e-mail the shops' daily summaries")
        parser.add_argument('--skip-reminders', action='store_true', help='Do not send plan reminders')
        parser.add_argument('--only', default='', help='Only this shop (its schema name, for example s_ramdeo, or public)')

    def handle(self, *args, **opts):
        from django_tenants.utils import schema_context
        connection.set_schema_to_public()
        failures = []
        if not opts['skip_reminders'] and not opts['only']:
            try:
                out = reminders.send_due()
                self.stdout.write(f"Reminders: {len(out['sent'])} sent, {len(out['failed'])} failed.")
                failures += [f'reminder {x}' for x in out['failed']]
            except Exception as e:                       # the backups below matter more
                self.stderr.write(self.style.ERROR(f'Reminders failed: {e}'))
        shops = list(Shop.objects.order_by('pk'))
        if opts['only']:
            shops = [s for s in shops if s.schema_name == opts['only']]
            if not shops:
                raise CommandError(f"No shop with the schema name '{opts['only']}'.")
        done = 0
        for shop in shops:
            main = shop.schema_name == 'public'
            name = 'main shop' if main else shop.schema_name
            state = 'active' if main else tenancy.shop_state(shop)
            try:
                with schema_context(shop.schema_name):
                    if not opts['skip_backup']:                 # every shop is backed up, also one that is locked or switched off
                        try:
                            path = backup.make_backup(keep=opts['keep'])
                            self.stdout.write(self.style.SUCCESS(f'[{name}] backup created: {path.name}'))
                        except backup.BackupError as e:
                            failures.append(f'backup {name}')
                            self.stderr.write(self.style.ERROR(f'[{name}] backup failed: {e}'))
                    if state in ('trial', 'active'):             # no automatic entries for a locked or switched-off shop
                        extra = ['--skip-email'] if opts['skip_email'] else []
                        call_command('daily_tasks', '--skip-backup', *extra, stdout=self.stdout, stderr=self.stderr)
                done += 1
            except Exception as e:
                failures.append(f'shop {name}')
                self.stderr.write(self.style.ERROR(f'[{name}] failed: {e}'))
            finally:
                connection.set_schema_to_public()
        self.stdout.write(f'{done} of {len(shops)} shop(s) done.')
        if failures:
            raise CommandError('Problems with: ' + ', '.join(failures))
