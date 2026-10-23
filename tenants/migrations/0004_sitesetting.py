from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_payment'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('whatsapp', models.CharField(blank=True, default='', max_length=20)),
                ('contact_email', models.EmailField(blank=True, default='', max_length=254)),
                ('announcement', models.CharField(blank=True, default='', max_length=200)),
                ('price_monthly', models.PositiveIntegerField(blank=True, null=True)),
                ('price_yearly', models.PositiveIntegerField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
