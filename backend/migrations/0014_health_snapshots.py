from django.db import migrations, models


class Migration(migrations.Migration):
    """A table that remembers the health score measured at different times. Nothing else changes."""

    dependencies = [
        ('backend', '0013_held_bills'),
    ]

    operations = [
        migrations.CreateModel(
            name='HealthSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('score', models.PositiveIntegerField(default=0)),
                ('data', models.JSONField(default=dict)),
            ],
            options={
                'verbose_name': 'Health Snapshot',
                'verbose_name_plural': 'Health Snapshots',
                'ordering': ['-id'],
            },
        ),
    ]
