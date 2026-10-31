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
    # What goes on the invoice (the customer fills it in on the Plan page before the first payment)
    bill_name = models.CharField(max_length=120, blank=True, default='')
    bill_country = models.CharField(max_length=2, blank=True, default='')        # ISO code, 'IN' = India; blank = not given yet
    bill_state = models.CharField(max_length=2, blank=True, default='')          # GST state code, for India only
    bill_address = models.TextField(blank=True, default='')
    bill_tax_id = models.CharField(max_length=30, blank=True, default='')        # GST number (India) or VAT / company number

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
    upi_id = models.CharField(max_length=80, blank=True, default='')           # where shops pay by UPI, e.g. medsync@okhdfcbank
    upi_name = models.CharField(max_length=40, blank=True, default='')         # the name shown in the payer's UPI app
    # The seller on every invoice (the platform owner's business). Without a GST number invoices carry no tax.
    seller_name = models.CharField(max_length=120, blank=True, default='')
    seller_address = models.TextField(blank=True, default='')
    seller_gstin = models.CharField(max_length=15, blank=True, default='')
    seller_lut = models.CharField(max_length=40, blank=True, default='')       # LUT number, printed on export invoices
    seller_sac = models.CharField(max_length=8, blank=True, default='')        # service accounting code printed on invoices
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return 'Website settings'


class PlanOrder(models.Model):
    """A shop's wish to pay for a plan: through Razorpay (paid at once) or by UPI (the owner types the UPI reference and the
    platform owner approves it once the money is seen). A paid order is linked to the Payment it created."""
    PLANS = [('monthly', 'Monthly'), ('yearly', 'Yearly')]
    METHODS = [('razorpay', 'Online (Razorpay)'), ('upi', 'UPI')]
    STATUSES = [('created', 'Started'), ('waiting', 'Waiting for approval'), ('paid', 'Paid'), ('rejected', 'Rejected'),
                ('cancelled', 'Cancelled')]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='orders')
    plan = models.CharField(max_length=10, choices=PLANS)
    amount = models.DecimalField(max_digits=10, decimal_places=2)                # rupees, GST included
    method = models.CharField(max_length=10, choices=METHODS)
    status = models.CharField(max_length=10, choices=STATUSES, default='created')
    reference = models.CharField(max_length=20, unique=True)                      # short code the payer sees, e.g. MS7K2Q9XAB
    razorpay_payment_id = models.CharField(max_length=40, blank=True, default='')
    utr = models.CharField(max_length=25, blank=True, default='')                 # the UPI reference the owner typed
    note = models.CharField(max_length=200, blank=True, default='')               # who approved, or why it was rejected
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    autopay = models.ForeignKey('AutoPay', null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')   # the subscription it was charged by
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.shop} {self.get_plan_display()} {self.amount} ({self.get_status_display()})'


class AutoPay(models.Model):
    """A Razorpay Subscription: the shop agreed once (UPI autopay or card) and Razorpay charges every month / year. Each
    charge becomes a PlanOrder + Payment, so the plan is extended by the same code as every other payment."""
    STATUSES = [('created', 'Started'), ('active', 'Active'), ('cancelled', 'Cancelled'), ('halted', 'Payment failed'),
                ('completed', 'Completed')]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='autopays')
    plan = models.CharField(max_length=10, choices=PlanOrder.PLANS)
    amount = models.DecimalField(max_digits=10, decimal_places=2)                # rupees per charge, GST included
    razorpay_subscription_id = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=10, choices=STATUSES, default='created')
    next_charge = models.DateField(null=True, blank=True)                         # told by Razorpay with each charge
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.shop} autopay {self.get_plan_display()} ({self.get_status_display()})'


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


class InvoiceCounter(models.Model):
    """The last invoice number used in a financial year (April to March). Locked while a number is taken, so numbers
    never repeat and never skip."""
    fy = models.CharField(max_length=7, unique=True)                             # '2026-27'
    last = models.PositiveIntegerField(default=0)


class Invoice(models.Model):
    """The tax invoice of one payment. Everything is copied onto it when it is made, so changing the seller details or the
    shop's billing details later never changes an invoice that was already issued."""
    KINDS = [('cgst_sgst', 'CGST + SGST'), ('igst', 'IGST'), ('export', 'Export (zero-rated)'), ('none', 'No tax')]

    number = models.CharField(max_length=16, unique=True)                        # MS/2026-27/0001 (at most 16 characters, as GST allows)
    fy = models.CharField(max_length=7)
    date = models.DateField()
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='invoices')
    payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice')
    plan = models.CharField(max_length=10)
    description = models.CharField(max_length=200)
    period_from = models.DateField()
    period_to = models.DateField()
    seller_name = models.CharField(max_length=120, blank=True, default='')
    seller_address = models.TextField(blank=True, default='')
    seller_gstin = models.CharField(max_length=15, blank=True, default='')
    seller_lut = models.CharField(max_length=40, blank=True, default='')
    sac = models.CharField(max_length=8, blank=True, default='')
    buyer_name = models.CharField(max_length=120, blank=True, default='')
    buyer_address = models.TextField(blank=True, default='')
    buyer_country = models.CharField(max_length=2, default='IN')
    buyer_state = models.CharField(max_length=2, blank=True, default='')
    buyer_tax_id = models.CharField(max_length=30, blank=True, default='')
    kind = models.CharField(max_length=10, choices=KINDS)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)       # percent
    taxable = models.DecimalField(max_digits=10, decimal_places=2)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number
