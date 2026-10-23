import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0002_pendingsignup'),
    ]

    operations = [
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plan', models.CharField(choices=[('monthly', 'Monthly'), ('yearly', 'Yearly')], max_length=10)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('paid_on', models.DateField()),
                ('period_from', models.DateField()),
                ('period_to', models.DateField()),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('shop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='tenants.shop')),
            ],
            options={
                'ordering': ['-paid_on', '-id'],
            },
        ),
    ]
