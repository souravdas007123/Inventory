import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Shop',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('schema_name', models.CharField(db_index=True, max_length=63, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('owner_name', models.CharField(blank=True, default='', max_length=120)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=20)),
                ('plan', models.CharField(choices=[('trial', 'Free trial'), ('monthly', 'Monthly'), ('yearly', 'Yearly')], default='trial', max_length=10)),
                ('trial_ends', models.DateField(blank=True, null=True)),
                ('paid_until', models.DateField(blank=True, null=True)),
                ('is_paused', models.BooleanField(default=False, help_text='Tick to switch the shop off without deleting anything.')),
                ('email_verified', models.BooleanField(default=False)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
                ('notes', models.TextField(blank=True, default='')),
            ],
            options={
                'ordering': ['-created_on'],
            },
        ),
        migrations.CreateModel(
            name='Domain',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(db_index=True, max_length=253, unique=True)),
                ('is_primary', models.BooleanField(db_index=True, default=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='domains', to='tenants.shop')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
