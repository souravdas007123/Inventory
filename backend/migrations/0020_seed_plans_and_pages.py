from django.db import migrations


def add_first_versions(apps, schema_editor):
    """Adds the first plans (without prices) and text pages once. Anything that already exists is left exactly as it is."""
    from backend.site_seed import PAGES, PLANS
    Plan, SitePage = apps.get_model('backend', 'Plan'), apps.get_model('backend', 'SitePage')
    if not Plan.objects.exists():
        for plan in PLANS:
            Plan.objects.create(**plan)
    for page in PAGES:
        SitePage.objects.get_or_create(slug=page['slug'], defaults=dict(title=page['title'], body=page['body'].strip()))


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0019_plans_pages_subscribers'),
    ]

    operations = [
        migrations.RunPython(add_first_versions, migrations.RunPython.noop),
    ]
