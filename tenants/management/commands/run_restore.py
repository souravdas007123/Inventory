from django.core.management.base import BaseCommand, CommandError

from tenants import restore


class Command(BaseCommand):
    help = ("Runs a shop restore that the platform panel has started (it starts this command itself). "
            "--recover puts a stuck shop back as it was; --cleanup removes saved copies older than 7 days.")

    def add_arguments(self, parser):
        parser.add_argument('request_id', nargs='?', type=int)
        parser.add_argument('--recover', action='store_true')
        parser.add_argument('--undo', action='store_true')
        parser.add_argument('--cleanup', action='store_true')

    def handle(self, *args, **opts):
        try:
            if opts['cleanup']:
                gone = restore.cleanup()
                self.stdout.write(f'Removed {len(gone)} saved cop{"y" if len(gone) == 1 else "ies"}: {", ".join(gone) or "-"}')
                return
            if not opts['request_id']:
                raise CommandError('Give the restore request number.')
            if opts['recover']:
                restore.recover(opts['request_id'])
                self.stdout.write(self.style.SUCCESS('The shop was put back as it was.'))
            elif opts['undo']:
                restore.undo(opts['request_id'])
                self.stdout.write(self.style.SUCCESS('Undone.'))
            else:
                ok = restore.execute(opts['request_id'])
                if not ok:
                    raise CommandError('The restore did not work. See the request in the platform panel.')
                self.stdout.write(self.style.SUCCESS('Restored.'))
        except restore.RestoreError as e:
            raise CommandError(str(e))
