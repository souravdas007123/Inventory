from django.db import migrations, models


class Migration(migrations.Migration):
    """Only the wording of one choice changes ('Damaged / broken'). The database itself is not touched."""

    dependencies = [
        ('backend', '0017_blog_images'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockwriteoff', name='reason',
            field=models.CharField(choices=[('EXPIRED', 'Expired'), ('DAMAGED', 'Damaged / broken'), ('OTHER', 'Other')], default='EXPIRED', max_length=10),
        ),
    ]
