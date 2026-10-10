from decimal import Decimal

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def sync_sale_dates(apps, schema_editor):
    """Purani Sale rows ki date ko unke Bill ki date se match karo (pehle hamesha 'aaj' ki date lagti thi)."""
    Sale = apps.get_model('backend', 'Sale')
    Transaction = apps.get_model('backend', 'Transaction')
    qs = Sale.objects.filter(invoice_item__bill__date__isnull=False).select_related('invoice_item__bill')
    for sale in qs:
        bill_date = sale.invoice_item.bill.date
        if sale.date != bill_date:
            sale.date = bill_date
            sale.save(update_fields=['date'])
            Transaction.objects.filter(sale=sale).update(sale_date=bill_date)


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0002_invoicecounter_shopprofile_bill_cancelled_at_and_more'),
    ]

    operations = [
        # ---- Sale.date: auto_now_add -> default (Bill ki date) ----
        migrations.AlterField(
            model_name='sale',
            name='date',
            field=models.DateField(default=django.utils.timezone.localdate, null=True),
        ),
        migrations.RunPython(sync_sale_dates, migrations.RunPython.noop),

        # ---- Paisa: Integer -> Decimal (paise na kate) ----
        migrations.AlterField(
            model_name='supplier',
            name='opening_balance',
            field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=12, null=True, verbose_name='Balance'),
        ),
        migrations.AlterField(
            model_name='purchaseitem',
            name='purchase_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Price (GST ke saath total)'),
        ),
        migrations.AlterField(
            model_name='purchaseitem',
            name='rate',
            field=models.DecimalField(decimal_places=4, default=0, editable=False, max_digits=12, verbose_name='Rate (per base unit)'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='amount',
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
        migrations.AlterField(
            model_name='payment',
            name='due',
            field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=12, null=True),
        ),
        migrations.AlterField(
            model_name='expense',
            name='amount',
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),

        # ---- GST choices: 0% aur 12% add ----
        migrations.AlterField(
            model_name='product',
            name='gst_rate',
            field=models.DecimalField(
                choices=[(Decimal('0.00'), '0% (Nil / Exempt)'), (Decimal('5.00'), '5%'),
                         (Decimal('12.00'), '12%'), (Decimal('18.00'), '18%')],
                decimal_places=2, default=Decimal('18.00'), max_digits=4, verbose_name='GST (%)'),
        ),

        # ---- Naye Product fields ----
        migrations.AddField(
            model_name='product',
            name='schedule',
            field=models.CharField(
                choices=[('NONE', 'Normal (no schedule)'), ('H', 'Schedule H (prescription)'),
                         ('H1', 'Schedule H1 (register zaroori)'), ('X', 'Schedule X (register zaroori)')],
                default='NONE', max_length=4, verbose_name='Drug Schedule'),
        ),
        migrations.AddField(
            model_name='product',
            name='barcode',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True, verbose_name='Barcode'),
        ),
        migrations.AddField(
            model_name='product',
            name='mrp',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                help_text='Billing me scan/search karne par rate yahin se bharta hai',
                verbose_name='MRP / Sale rate (per base unit)'),
        ),

        # ---- CASCADE -> PROTECT (parent delete par history na udhe) ----
        migrations.AlterField(
            model_name='purchase',
            name='supplier',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='backend.supplier'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='supplier',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='backend.supplier'),
        ),
        migrations.AlterField(
            model_name='purchaseorder',
            name='supplier',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='backend.supplier'),
        ),
        migrations.AlterField(
            model_name='invoiceitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='backend.product'),
        ),
        migrations.AlterField(
            model_name='order',
            name='customer_product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='backend.product'),
        ),
    ]
