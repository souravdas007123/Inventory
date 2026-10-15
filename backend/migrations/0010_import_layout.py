from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0009_reorder_level_automatic'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportLayout',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=20)),
                ('signature', models.CharField(max_length=40)),
                ('mapping', models.TextField(default='{}', help_text='JSON: file header -> our column ("" = ignored)')),
                ('headers', models.TextField(blank=True, default='')),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Import file layout',
                'verbose_name_plural': 'Import file layouts',
                'unique_together': {('kind', 'signature')},
            },
        ),
    ]
