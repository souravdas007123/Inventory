import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0009_tickets')]
    operations = [
        migrations.AddField(model_name='sitesetting', name='upi_id',
                            field=models.CharField(blank=True, default='', max_length=80)),
        migrations.AddField(model_name='sitesetting', name='upi_name',
                            field=models.CharField(blank=True, default='', max_length=40)),
        migrations.CreateModel(
            name='PlanOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan', models.CharField(choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')], max_length=10)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('method', models.CharField(choices=[('razorpay', 'Online (Razorpay)'), ('upi', 'UPI')], max_length=10)),
                ('status', models.CharField(choices=[('created', 'Started'), ('waiting', 'Waiting for approval'), ('paid', 'Paid'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='created', max_length=10)),
                ('reference', models.CharField(max_length=20, unique=True)),
                ('razorpay_order_id', models.CharField(blank=True, db_index=True, default='', max_length=40)),
                ('razorpay_payment_id', models.CharField(blank=True, default='', max_length=40)),
                ('utr', models.CharField(blank=True, default='', max_length=25)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='tenants.payment')),
                ('shop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='orders', to='tenants.shop')),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
    ]
