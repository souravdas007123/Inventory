from decimal import Decimal

import django.core.validators
from django.db import migrations, models
from django.db.models import F


def full_credit_for_old_returns(apps, schema_editor):
    """Every return made before this change got the full value from the supplier: keep it so."""
    apps.get_model('backend', 'PurchaseReturnItem').objects.update(credit=F('amount'), credit_percent=Decimal('100'))


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0024_social_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasereturnitem',
            name='credit_percent',
            field=models.DecimalField(decimal_places=2, default=Decimal('100'), max_digits=5, validators=[
                django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('100'))],
                verbose_name='Supplier credit (%)'),
        ),
        migrations.AddField(
            model_name='purchasereturnitem',
            name='credit',
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12, verbose_name='Credit from supplier'),
        ),
        migrations.RunPython(full_credit_for_old_returns, migrations.RunPython.noop),
    ]
