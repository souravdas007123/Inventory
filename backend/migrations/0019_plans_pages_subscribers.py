import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Tables for the pricing plans, the website text pages and the subscriber register. Nothing else changes."""

    dependencies = [
        ('backend', '0018_writeoff_reason_label'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('tagline', models.CharField(blank=True, default='', help_text="One short line under the name, e.g. 'For a single counter'.", max_length=160)),
                ('monthly_price', models.DecimalField(blank=True, decimal_places=2, help_text='Rupees per month. Empty = not shown.', max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('yearly_price', models.DecimalField(blank=True, decimal_places=2, help_text='Rupees per year. Empty = not shown.', max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('setup_fee', models.DecimalField(blank=True, decimal_places=2, help_text='One-time setup fee in rupees. Empty = not shown.', max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('price_note', models.CharField(blank=True, default='', help_text="Shown under the price, e.g. 'GST extra'.", max_length=160)),
                ('features', models.TextField(blank=True, default='', help_text='One feature per line.')),
                ('highlight', models.BooleanField(default=False, help_text='Mark as the plan most shops choose.')),
                ('is_active', models.BooleanField(default=True, help_text='Untick to hide the plan from the website.')),
                ('sort_order', models.PositiveIntegerField(default=10, help_text='Smaller numbers come first.')),
            ],
            options={'verbose_name': 'Subscription Plan', 'verbose_name_plural': 'Subscription Plans', 'ordering': ['sort_order', 'id']},
        ),
        migrations.CreateModel(
            name='SitePage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.CharField(choices=[('terms', 'Terms of Service'), ('privacy', 'Privacy Policy'), ('refund', 'Refund and Cancellation'), ('pricing-included', 'Pricing page: included in every plan')], max_length=30, unique=True)),
                ('title', models.CharField(max_length=120)),
                ('body', models.TextField(help_text="Plain text. Blank line = new paragraph. '## Heading', '- ' for a bullet list, '1. ' for numbers, **bold**, [text](https://...). Have a lawyer or your CA read the legal pages before you rely on them.")),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Website Text Page', 'verbose_name_plural': 'Website Text Pages', 'ordering': ['slug']},
        ),
        migrations.CreateModel(
            name='Subscriber',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('shop_name', models.CharField(max_length=150)),
                ('contact_person', models.CharField(blank=True, default='', max_length=100)),
                ('phone', models.CharField(blank=True, default='', max_length=20)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('city', models.CharField(blank=True, default='', max_length=80)),
                ('billing', models.CharField(choices=[('MONTHLY', 'Monthly'), ('YEARLY', 'Yearly')], default='MONTHLY', max_length=10)),
                ('amount', models.DecimalField(blank=True, decimal_places=2, help_text='What this shop pays each period, in rupees.', max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('status', models.CharField(choices=[('TRIAL', 'Trial / demo'), ('ACTIVE', 'Active'), ('PAUSED', 'Paused'), ('CLOSED', 'Closed')], default='TRIAL', max_length=10)),
                ('started_on', models.DateField(default=django.utils.timezone.localdate)),
                ('renews_on', models.DateField(blank=True, help_text='The next payment is due on this date.', null=True)),
                ('server_url', models.CharField(blank=True, default='', help_text="Where this shop's MedSync runs, e.g. https://shopname.pythonanywhere.com", max_length=200)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subscribers', to='backend.plan')),
            ],
            options={'verbose_name': 'Subscriber (Customer Shop)', 'verbose_name_plural': 'Subscribers (Customer Shops)', 'ordering': ['shop_name']},
        ),
    ]
