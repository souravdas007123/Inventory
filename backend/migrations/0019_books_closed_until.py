from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0018_writeoff_reason_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='shopprofile',
            name='books_closed_until',
            field=models.DateField(blank=True, null=True, verbose_name='Books closed up to',
                                   help_text='Bills and purchases dated on or before this day cannot be added, edited, cancelled or deleted (for example set it to the last day of last month after your CA has taken the figures). Clear it to reopen.'),
        ),
    ]
