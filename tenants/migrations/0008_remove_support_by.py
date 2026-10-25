from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('tenants', '0007_shop_notice_support')]
    operations = [migrations.RemoveField(model_name='logintoken', name='support_by')]
