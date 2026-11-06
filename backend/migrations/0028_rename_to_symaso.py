from django.db import migrations

OLD, NEW = 'MedSync', 'Symaso'


def rename_in_site_content(apps, schema_editor):
    """The articles, FAQ and policy pages were copied into the database with the old name. This changes the old name to the
    new one in those rows. Everything else in the text stays exactly as the owner left it. Only on the main site."""
    if getattr(schema_editor.connection, 'schema_name', 'public') != 'public':
        return
    targets = {'BlogPost': ('title', 'summary', 'body', 'image_alt'),
               'FaqItem': ('question', 'answer'),
               'LegalPage': ('description', 'body')}
    for model_name, fields in targets.items():
        model = apps.get_model('backend', model_name)
        for obj in model.objects.all():
            changed = []
            for f in fields:
                value = getattr(obj, f) or ''
                if OLD in value:
                    setattr(obj, f, value.replace(OLD, NEW))
                    changed.append(f)
            if changed:
                obj.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0027_offline_bill'),
    ]

    operations = [
        migrations.RunPython(rename_in_site_content, migrations.RunPython.noop),
    ]
