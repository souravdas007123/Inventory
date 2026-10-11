from django.db import migrations, models


class Migration(migrations.Migration):
    """Selling price (MRP) entered on a purchase item; Product.mrp keeps 4 decimals so a per-unit MRP derived from a
    pack price (e.g. Rs 62.75 / 15 tablets) bills back to exactly the pack price."""

    dependencies = [
        ('backend', '0005_auditlog_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseitem',
            name='selling_price',
            field=models.DecimalField(blank=True, decimal_places=2, help_text="MRP of 1 purchase unit (e.g. 1 Strip) including GST. Optional: it updates the product's MRP, which fills the billing rate", max_digits=10, null=True, verbose_name='Selling price (MRP incl. GST)'),
        ),
        migrations.AlterField(
            model_name='product',
            name='mrp',
            field=models.DecimalField(blank=True, decimal_places=4, help_text='MRP of 1 base unit including GST. Billing works out its rate from this automatically (GST is taken out of the MRP)', max_digits=12, null=True, verbose_name='MRP incl. GST (per base unit)'),
        ),
    ]
