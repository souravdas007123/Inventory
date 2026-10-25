import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0008_remove_support_by')]
    operations = [
        migrations.CreateModel(
            name='Ticket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(max_length=120)),
                ('category', models.CharField(choices=[('billing', 'Billing'), ('stock', 'Stock and batches'), ('login', 'Sign-in and users'), ('reports', 'Reports and GST'), ('idea', 'Idea or feature request'), ('other', 'Something else')], default='other', max_length=10)),
                ('status', models.CharField(choices=[('open', 'Open'), ('answered', 'Answered'), ('closed', 'Closed')], default='open', max_length=10)),
                ('created_by', models.CharField(blank=True, default='', max_length=150)),
                ('shop_unread', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('shop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tickets', to='tenants.shop')),
            ],
            options={'ordering': ['-updated_at']},
        ),
        migrations.CreateModel(
            name='TicketMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_shop', models.BooleanField(default=True)),
                ('author', models.CharField(blank=True, default='', max_length=150)),
                ('body', models.TextField(max_length=4000)),
                ('page', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='tenants.ticket')),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
    ]
