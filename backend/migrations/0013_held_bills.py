from django.db import migrations, models


class Migration(migrations.Migration):
    """A table for parked ("hold") bills. Nothing else changes."""

    dependencies = [
        ('backend', '0012_admin_serial_numbers'),
    ]

    operations = [
        migrations.CreateModel(
            name='HeldBill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.CharField(blank=True, default='', max_length=150)),
                ('label', models.CharField(blank=True, default='', max_length=150)),
                ('items_count', models.PositiveIntegerField(default=0)),
                ('payload', models.JSONField(default=list)),
            ],
            options={
                'verbose_name': 'Held Bill',
                'verbose_name_plural': 'Held Bills',
                'ordering': ['-id'],
            },
        ),
    ]
