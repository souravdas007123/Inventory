"""Gives the usual Units and Categories to shops that were made before new shops got them.

By default only a shop that has NO units and NO categories at all is filled, so nobody gets back what they removed on purpose.
    python manage.py seed_shop_defaults --dry-run     show what would happen
    python manage.py seed_shop_defaults               fill the empty shops
    python manage.py seed_shop_defaults --all         add the missing ones to every shop (never removes or renames anything)
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from backend import defaults
from backend.models import Category, Unit
from tenants.models import Shop


class Command(BaseCommand):
    help = 'Adds the usual Units and Categories to shops that have none.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='only show what would be added')
        parser.add_argument('--all', action='store_true', help='add the missing ones even to shops that already have some')

    def handle(self, *args, **options):
        dry, everyone = options['dry_run'], options['all']
        filled = skipped = 0
        for shop in Shop.objects.exclude(schema_name='public').order_by('pk'):
            with schema_context(shop.schema_name):
                has = Unit.objects.exists() or Category.objects.exists()
                if has and not everyone:
                    skipped += 1
                    self.stdout.write(f'{shop.schema_name}: has its own, left alone')
                    continue
                if dry:
                    self.stdout.write(f'{shop.schema_name}: would be filled')
                    filled += 1
                    continue
                units, cats = defaults.add_missing()
            filled += 1
            self.stdout.write(self.style.SUCCESS(f'{shop.schema_name}: {units} units and {cats} categories added'))
        self.stdout.write(f"{'Would fill' if dry else 'Filled'} {filled}, left alone {skipped}.")
