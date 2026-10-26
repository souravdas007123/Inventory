from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from backend import twofactor


class Command(BaseCommand):
    help = ("Switches 2-step sign-in off for one user who lost both his e-mail and his recovery codes. "
            "One shop: python manage.py two_step_off <username>. Many shops: "
            "python manage.py tenant_command two_step_off --schema=<s_shopname> <username> (main site: the same without tenant_command).")

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options['username']).first()
        if user is None:
            raise CommandError(f"There is no user '{options['username']}' here.")
        if not twofactor.is_on(user):
            self.stdout.write(f"2-step sign-in was already off for {user.username}.")
            return
        twofactor.turn_off(user)
        self.stdout.write(f"2-step sign-in is now off for {user.username}.")
