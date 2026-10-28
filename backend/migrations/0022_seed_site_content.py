from django.db import migrations


def add_first_content(apps, schema_editor):
    """Copies the first FAQ and the text of the three policy pages into the database, once. Anything that already exists is
    never touched, so the owner's edits are safe. Only on the main site (a shop's own schema does not use them)."""
    if getattr(schema_editor.connection, 'schema_name', 'public') != 'public':
        return
    from backend.site_seed import FAQS, LEGAL_BODIES, LEGAL_DESCRIPTIONS
    from django.utils import timezone
    FaqItem, LegalPage = apps.get_model('backend', 'FaqItem'), apps.get_model('backend', 'LegalPage')
    if not FaqItem.objects.exists():
        for i, (question, answer) in enumerate(FAQS, start=1):
            FaqItem.objects.create(question=question, answer=answer, position=i * 10)
    for key, body in LEGAL_BODIES.items():
        LegalPage.objects.get_or_create(key=key, defaults=dict(
            body=body.strip(), description=LEGAL_DESCRIPTIONS[key], updated_on=timezone.localdate().replace(day=1)))


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0021_site_content'),
    ]

    operations = [
        migrations.RunPython(add_first_content, migrations.RunPython.noop),
    ]
