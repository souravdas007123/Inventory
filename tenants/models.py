"""The shops of the platform. Only installed when MULTI_SHOP=1 (see vaultyfly/settings.py).

A Shop is a tenant: django-tenants gives it its own PostgreSQL schema (all tables of the app) and the Domain rows say
which web addresses open it. The shop's own users and data live inside its schema."""
from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Shop(TenantMixin):
    PLANS = [('trial', 'Free trial'), ('monthly', 'Monthly'), ('yearly', 'Yearly')]

    # (schema names are made by this app as s_<name>, so the library's own name check is not needed here)
    schema_name = models.CharField(max_length=63, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    owner_name = models.CharField(max_length=120, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    plan = models.CharField(max_length=10, choices=PLANS, default='trial')
    trial_ends = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    is_paused = models.BooleanField(default=False, help_text='Tick to switch the shop off without deleting anything.')
    email_verified = models.BooleanField(default=False)
    created_on = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default='')

    auto_create_schema = True            # the schema and its tables are made when the shop is first saved
    auto_drop_schema = False             # deleting a shop row never deletes its data by accident

    class Meta:
        ordering = ['-created_on']

    def __str__(self):
        return self.name or self.schema_name


class Domain(DomainMixin):
    pass
