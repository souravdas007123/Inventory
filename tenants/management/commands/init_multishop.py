"""Run once after `migrate_schemas --shared`: makes the "public" shop (the main site and its data) and its address.

    python manage.py init_multishop            # address = SHOP_BASE_DOMAIN from .env (localhost when not set)

It is safe to run again: nothing is duplicated."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tenants.models import Domain, Shop


class Command(BaseCommand):
    help = 'Creates the public shop and its web address (safe to run again).'

    def handle(self, *args, **opts):
        if not getattr(settings, 'MULTI_SHOP', False):
            raise CommandError('MULTI_SHOP is not switched on. Put MULTI_SHOP=1 in .env first.')
        shop, made = Shop.objects.get_or_create(
            schema_name='public',
            defaults=dict(name='MedSync main site', plan='yearly', email_verified=True))
        domain, new_domain = Domain.objects.get_or_create(
            domain=settings.SHOP_BASE_DOMAIN, defaults=dict(tenant=shop, is_primary=True))
        if domain.tenant_id != shop.pk:
            raise CommandError(f'The address {domain.domain} already belongs to another shop.')
        self.stdout.write(self.style.SUCCESS(
            f"Public shop {'created' if made else 'already there'}; address {domain.domain} "
            f"{'added' if new_domain else 'already there'}."))
