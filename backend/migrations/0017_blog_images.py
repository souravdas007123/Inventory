from django.db import migrations, models


def set_covers(apps, schema_editor):
    """Gives the first articles their illustrations. Only articles that have no cover yet are touched."""
    from backend.blog_seed import POSTS
    BlogPost = apps.get_model('backend', 'BlogPost')
    for post in POSTS:
        BlogPost.objects.filter(slug=post['slug'], cover='', image_url='').update(cover=post['cover'])


class Migration(migrations.Migration):
    """Pictures for blog articles: a ready-made illustration or an own picture address."""

    dependencies = [
        ('backend', '0016_seed_blog_posts'),
    ]

    operations = [
        migrations.AddField(
            model_name='blogpost', name='cover',
            field=models.CharField(blank=True, choices=[('', 'None'), ('expiry', 'Expiry'), ('gst', 'GST'), ('register', 'Drug register'), ('fefo', 'FEFO'), ('prices', 'Supplier prices'), ('import', 'Excel import')],
                                   default='', help_text='A ready-made illustration shown at the top of the article. Ignored when you give a picture address below.', max_length=20),
        ),
        migrations.AddField(
            model_name='blogpost', name='image_url',
            field=models.CharField(blank=True, default='', help_text='Optional real picture for the top of the article: an address starting with https:// or / (for example a screenshot). It replaces the illustration.', max_length=300),
        ),
        migrations.AddField(
            model_name='blogpost', name='image_alt',
            field=models.CharField(blank=True, default='', help_text='Describe the picture in a few words (for visitors who cannot see it and for Google).', max_length=200),
        ),
        migrations.RunPython(set_covers, migrations.RunPython.noop),
    ]
