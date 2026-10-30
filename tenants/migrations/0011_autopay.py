import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0010_planorder_upi')]
    operations = [
        migrations.CreateModel(
            name='AutoPay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan', models.CharField(choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')], max_length=10)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('razorpay_subscription_id', models.CharField(max_length=40, unique=True)),
                ('status', models.CharField(choices=[('created', 'Started'), ('active', 'Active'), ('cancelled', 'Cancelled'), ('halted', 'Payment failed'), ('completed', 'Completed')], default='created', max_length=10)),
                ('next_charge', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('shop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='autopays', to='tenants.shop')),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddField(model_name='planorder', name='autopay',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='tenants.autopay')),
        migrations.RemoveField(model_name='planorder', name='razorpay_order_id'),
    ]
