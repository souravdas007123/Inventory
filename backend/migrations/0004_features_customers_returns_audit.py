from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0003_fixes_decimal_protect_product_fields'),
    ]

    operations = [
        # ---------------- Customer + udhar ----------------
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('phone', models.CharField(blank=True, max_length=15, null=True)),
                ('address', models.TextField(blank=True, null=True)),
                ('gstin', models.CharField(blank=True, help_text='B2B bill ke liye (optional)', max_length=15, null=True, verbose_name='GSTIN')),
                ('credit_limit', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Udhar ki limit. 0 = koi limit nahi', max_digits=12)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateField(auto_now_add=True, null=True)),
            ],
            options={
                'verbose_name': 'Customer',
                'verbose_name_plural': 'Customers',
            },
        ),
        migrations.CreateModel(
            name='CustomerPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('mode', models.CharField(choices=[('CASH', 'Cash'), ('UPI', 'UPI'), ('CARD', 'Card'), ('BANK', 'Bank')], default='CASH', max_length=10)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='backend.customer')),
            ],
            options={
                'verbose_name': 'Customer Payment',
                'verbose_name_plural': 'Customer Payments',
            },
        ),
        migrations.AddField(
            model_name='bill',
            name='customer',
            field=models.ForeignKey(blank=True, help_text='Regular customer / udhar ke liye select karein', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='bills', to='backend.customer'),
        ),
        migrations.AddField(
            model_name='bill',
            name='payment_mode',
            field=models.CharField(choices=[('CASH', 'Cash'), ('UPI', 'UPI'), ('CARD', 'Card'), ('CREDIT', 'Udhar (Credit)')], default='CASH', max_length=10),
        ),
        migrations.AddField(
            model_name='bill',
            name='amount_paid',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Udhar bill me customer ne abhi kitna diya (baaki udhar me jayega)', max_digits=12, verbose_name='Abhi diya (sirf udhar bill me)'),
        ),
        migrations.AddField(
            model_name='bill',
            name='doctor_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='bill',
            name='doctor_reg_no',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='Doctor Reg. No.'),
        ),
        migrations.AddField(
            model_name='bill',
            name='patient_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='bill',
            name='patient_address',
            field=models.CharField(blank=True, default='', max_length=255),
        ),

        # ---------------- Document counter (CN / DN) ----------------
        migrations.CreateModel(
            name='DocCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('series', models.CharField(max_length=10)),
                ('fiscal_year', models.CharField(max_length=7)),
                ('last_number', models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.AddConstraint(
            model_name='doccounter',
            constraint=models.UniqueConstraint(fields=('series', 'fiscal_year'), name='unique_series_per_fy'),
        ),

        # ---------------- Sales Return (Credit Note) ----------------
        migrations.CreateModel(
            name='SalesReturn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note_number', models.CharField(blank=True, editable=False, max_length=30, null=True, unique=True)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('refund_mode', models.CharField(choices=[('CASH', 'Cash / UPI wapas diya'), ('ADJUST', 'Customer ke udhar me adjust')], default='CASH', max_length=10)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('bill', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='returns', to='backend.bill')),
            ],
            options={
                'verbose_name': 'Sales Return (Credit Note)',
                'verbose_name_plural': 'Sales Returns (Credit Notes)',
            },
        ),
        migrations.CreateModel(
            name='SalesReturnItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qty', models.IntegerField(help_text='Bill me jis unit me becha tha, usi me', verbose_name='Return Qty')),
                ('base_qty', models.PositiveIntegerField(default=0, editable=False)),
                ('taxable_value', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('gst', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('cgst', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('sgst', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('igst', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('total', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('batch_returns', models.JSONField(blank=True, default=dict, editable=False)),
                ('cost', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('invoice_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='return_items', to='backend.invoiceitem')),
                ('sales_return', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='backend.salesreturn')),
            ],
            options={
                'verbose_name': 'Sales Return Item',
                'verbose_name_plural': 'Sales Return Items',
            },
        ),
        migrations.AddField(
            model_name='sale',
            name='sales_return_item',
            field=models.OneToOneField(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sale_entry', to='backend.salesreturnitem'),
        ),

        # ---------------- Purchase Return (Debit Note) ----------------
        migrations.CreateModel(
            name='PurchaseReturn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note_number', models.CharField(blank=True, editable=False, max_length=30, null=True, unique=True)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('purchase', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='returns', to='backend.purchase')),
            ],
            options={
                'verbose_name': 'Purchase Return (Debit Note)',
                'verbose_name_plural': 'Purchase Returns (Debit Notes)',
            },
        ),
        migrations.CreateModel(
            name='PurchaseReturnItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qty', models.IntegerField(help_text='Purchase me jis unit me aaya tha, usi me', verbose_name='Return Qty')),
                ('base_qty', models.PositiveIntegerField(default=0, editable=False)),
                ('taxable_value', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('gst', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12)),
                ('amount', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=12, verbose_name='Amount (GST ke saath)')),
                ('purchase_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='return_items', to='backend.purchaseitem')),
                ('purchase_return', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='backend.purchasereturn')),
            ],
            options={
                'verbose_name': 'Purchase Return Item',
                'verbose_name_plural': 'Purchase Return Items',
            },
        ),
        migrations.AddField(
            model_name='transaction',
            name='purchase_return_item',
            field=models.OneToOneField(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, to='backend.purchasereturnitem'),
        ),

        # ---------------- Stock write-off ----------------
        migrations.CreateModel(
            name='StockWriteOff',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qty', models.PositiveIntegerField(help_text='Base unit me (jaise Tablet)', validators=[django.core.validators.MinValueValidator(1)])),
                ('reason', models.CharField(choices=[('EXPIRED', 'Expired'), ('DAMAGED', 'Damaged / toota-phoota'), ('OTHER', 'Other')], default='EXPIRED', max_length=10)),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('cost', models.DecimalField(decimal_places=2, default=0, editable=False, help_text='Is maal ki purchase cost (P&L me loss)', max_digits=12)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='writeoffs', to='backend.batch')),
            ],
            options={
                'verbose_name': 'Stock Write-off',
                'verbose_name_plural': 'Stock Write-offs',
            },
        ),

        # ---------------- Physical stock audit ----------------
        migrations.CreateModel(
            name='StockAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('OPEN', 'Open (ginti chal rahi hai)'), ('APPLIED', 'Applied (stock adjust ho gaya)')], default='OPEN', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Stock Audit',
                'verbose_name_plural': 'Stock Audits',
            },
        ),
        migrations.CreateModel(
            name='StockAuditItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('system_qty', models.PositiveIntegerField(help_text='Audit shuru hote waqt system me stock (base unit)')),
                ('counted_qty', models.PositiveIntegerField(blank=True, help_text='Asli ginti (base unit)', null=True)),
                ('system_qty_at_apply', models.PositiveIntegerField(blank=True, editable=False, null=True)),
                ('applied_diff', models.IntegerField(blank=True, editable=False, null=True)),
                ('audit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='backend.stockaudit')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='backend.product')),
            ],
        ),
        migrations.AddConstraint(
            model_name='stockaudititem',
            constraint=models.UniqueConstraint(fields=('audit', 'product'), name='unique_product_per_audit'),
        ),
    ]
