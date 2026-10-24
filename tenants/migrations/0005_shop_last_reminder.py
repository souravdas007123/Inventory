from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_sitesetting'),
    ]

    operations = [
        migrations.AddField(
            model_name='shop',
            name='last_reminder',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
