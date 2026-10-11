from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0004_features_customers_returns_audit'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.CharField(blank=True, default='', max_length=150)),
                ('action', models.CharField(max_length=20)),
                ('target', models.CharField(blank=True, default='', max_length=60)),
                ('detail', models.CharField(blank=True, default='', max_length=500)),
            ],
            options={
                'verbose_name': 'Audit Log',
                'verbose_name_plural': 'Audit Logs',
                'ordering': ['-id'],
            },
        ),
        migrations.AddIndex(
            model_name='batch',
            index=models.Index(fields=['expire_date'], name='batch_expire_idx'),
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(fields=['date'], name='sale_date_idx'),
        ),
        migrations.AddIndex(
            model_name='bill',
            index=models.Index(fields=['date', 'status'], name='bill_date_status_idx'),
        ),
    ]
