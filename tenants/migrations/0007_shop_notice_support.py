from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0006_logintoken')]
    operations = [
        migrations.AddField(model_name='sitesetting', name='shop_notice',
                            field=models.CharField(blank=True, default='', max_length=200)),
        migrations.AddField(model_name='logintoken', name='support_by',
                            field=models.CharField(blank=True, default='', max_length=150)),
    ]
