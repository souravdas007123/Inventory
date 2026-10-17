import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """A table for the website's blog articles. Nothing else changes."""

    dependencies = [
        ('backend', '0014_health_snapshots'),
    ]

    operations = [
        migrations.CreateModel(
            name='BlogPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('slug', models.SlugField(help_text='The address of the article: /blog/<slug>/. Use lower-case words with dashes.', max_length=120, unique=True)),
                ('summary', models.CharField(help_text='One or two sentences. Shown in the article list and used as the description on Google.', max_length=300)),
                ('body', models.TextField(help_text="Plain text. Blank line = new paragraph. '## Heading', '### Smaller heading', '- ' for a bullet list, '1. ' for a numbered list, **bold**, [link text](https://...).")),
                ('published', models.BooleanField(default=False, help_text='Tick to show the article on the website. Untick to keep it as a draft.')),
                ('published_at', models.DateTimeField(default=django.utils.timezone.now, help_text='Shown as the article date. A future date keeps it hidden until then.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Blog Article',
                'verbose_name_plural': 'Blog Articles',
                'ordering': ['-published_at', '-id'],
            },
        ),
    ]
