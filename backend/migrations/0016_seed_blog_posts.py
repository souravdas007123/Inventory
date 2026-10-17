from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def add_first_articles(apps, schema_editor):
    """Adds the first articles once. An article that already exists (same address) is never touched, so edits are safe."""
    from backend.blog_seed import POSTS
    BlogPost = apps.get_model('backend', 'BlogPost')
    now = timezone.now()
    for i, post in enumerate(POSTS):
        BlogPost.objects.get_or_create(
            slug=post['slug'],
            defaults=dict(title=post['title'], summary=post['summary'], body=post['body'].strip(),
                          published=True, published_at=now - timedelta(minutes=i)))


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0015_blog_posts'),
    ]

    operations = [
        migrations.RunPython(add_first_articles, migrations.RunPython.noop),
    ]
