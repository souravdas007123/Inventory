from django.core.management.base import BaseCommand

from backend import defaults


class Command(BaseCommand):
    help = "Adds common Units and Categories (running it again does not create duplicates)."

    def handle(self, *args, **options):
        units, cats = defaults.add_missing()
        self.stdout.write(self.style.SUCCESS(f"Unit: {units} added"))
        self.stdout.write(self.style.SUCCESS(f"Category: {cats} added"))
