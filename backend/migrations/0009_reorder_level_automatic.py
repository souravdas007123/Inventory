from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0008_shop_alert_phone'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='reorder_level',
            field=models.PositiveIntegerField(
                default=5, editable=False, verbose_name='Reorder level',
                help_text='Worked out automatically from sales (about 10 days of average sales). It cannot be edited.'),
        ),
    ]
