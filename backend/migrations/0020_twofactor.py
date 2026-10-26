import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('backend', '0019_books_closed_until'),
    ]
    operations = [
        migrations.CreateModel(
            name='TwoFactor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled_at', models.DateTimeField(auto_now_add=True)),
                ('recovery', models.JSONField(blank=True, default=list)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='two_factor', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': '2-step sign-in', 'verbose_name_plural': '2-step sign-in users'},
        ),
    ]
