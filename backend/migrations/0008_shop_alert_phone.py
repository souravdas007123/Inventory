from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0007_employees_recurring_expenses'),
    ]

    operations = [
        migrations.AddField(
            model_name='shopprofile',
            name='alert_phone',
            field=models.CharField(
                blank=True, max_length=30, verbose_name='Alerts WhatsApp number',
                help_text="Stock alerts open in this number's WhatsApp chat (for example the manager or staff). "
                          "Leave empty to use the shop phone, or to choose a contact each time."),
        ),
    ]
