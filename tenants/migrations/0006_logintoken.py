from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0005_shop_last_reminder'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=64, unique=True)),
                ('schema_name', models.CharField(max_length=63)),
                ('user_id', models.IntegerField()),
                ('next_url', models.CharField(blank=True, default='', max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used', models.BooleanField(default=False)),
            ],
        ),
    ]
