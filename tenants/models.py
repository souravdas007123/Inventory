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
    last_reminder = models.CharField(max_length=40, blank=True, default='')      # e.g. 'trial7:2026-11-21'; see tenancy.reminder_key

    auto_create_schema = True            # the schema and its tables are made when the shop is first saved
    auto_drop_schema = False             # deleting a shop row never deletes its data by accident

    class Meta:
        ordering = ['-created_on']

    def __str__(self):
        return self.name or self.schema_name


class Domain(DomainMixin):
    pass


class PendingSignup(models.Model):
    """A sign-up waiting for the owner to confirm his email. Nothing (no schema, no user) is made until then."""
    VALID_HOURS = 48

    token = models.CharField(max_length=64, unique=True)
    subdomain = models.CharField(max_length=30)
    shop_name = models.CharField(max_length=120)
    owner_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.shop_name} ({self.email})'

    def expired(self):
        from datetime import timedelta

        from django.utils import timezone
        return timezone.now() - self.created_at > timedelta(hours=self.VALID_HOURS)


class LoginToken(models.Model):
    """A one-time pass that carries a person from the main site's sign-in page into his own shop (valid for one minute)."""
    VALID_SECONDS = 60

    token = models.CharField(max_length=64, unique=True)
    schema_name = models.CharField(max_length=63)
    user_id = models.IntegerField()
    next_url = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)


class Payment(models.Model):
    """One payment received from a shop (entered by the platform owner when the money arrives)."""
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='payments')
    plan = models.CharField(max_length=10, choices=Shop.PLANS[1:])
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_on = models.DateField()
    period_from = models.DateField()
    period_to = models.DateField()
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']

    def __str__(self):
        return f'{self.shop} {self.amount} on {self.paid_on}'


class SiteSetting(models.Model):
    """The few things of the public website that the platform owner can change without touching the code.
    There is only one row (id 1). Blank prices mean 'use the standard price from the settings file'."""
    whatsapp = models.CharField(max_length=20, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    announcement = models.CharField(max_length=200, blank=True, default='')
    shop_notice = models.CharField(max_length=200, blank=True, default='')      # shown at the top of every shop
    price_monthly = models.PositiveIntegerField(null=True, blank=True)
    price_yearly = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return 'Website settings'


class Ticket(models.Model):
    """A question or problem that a shop sends to the platform owner from inside the app.
    open = waiting for MedSync, answered = waiting for the shop, closed = done."""
    CATEGORIES = [('billing', 'Billing'), ('stock', 'Stock and batches'), ('login', 'Sign-in and users'),
                  ('reports', 'Reports and GST'), ('idea', 'Idea or feature request'), ('other', 'Something else')]
    STATUSES = [('open', 'Open'), ('answered', 'Answered'), ('closed', 'Closed')]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='tickets')
    subject = models.CharField(max_length=120)
    category = models.CharField(max_length=10, choices=CATEGORIES, default='other')
    status = models.CharField(max_length=10, choices=STATUSES, default='open')
    created_by = models.CharField(max_length=150, blank=True, default='')
    shop_unread = models.BooleanField(default=False)          # MedSync wrote and the shop has not read it yet
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'#{self.pk} {self.subject}'


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    from_shop = models.BooleanField(default=True)
    author = models.CharField(max_length=150, blank=True, default='')
    body = models.TextField(max_length=4000)
    page = models.CharField(max_length=200, blank=True, default='')       # the page the shop was on when it wrote
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
