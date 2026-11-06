import importlib
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection
from django.test import TestCase

from backend.models_extra import BlogPost, FaqItem, LegalPage

MIG = importlib.import_module('backend.migrations.0028_rename_to_symaso')


class RenameTests(TestCase):
    def test_migration_renames_old_content_and_keeps_the_rest(self):
        BlogPost.objects.create(title='Why MedSync', slug='old-name', summary='MedSync saves time', body='Use MedSync.\n\nSecond MedSync line.')
        BlogPost.objects.create(title='Other', slug='other-name', summary='Nothing here', body='Plain text')
        FaqItem.objects.create(question='Is MedSync safe?', answer='Yes, MedSync is.')
        LegalPage.objects.update_or_create(key='terms', defaults=dict(description='MedSync terms', body='These are the MedSync terms.'))
        MIG.rename_in_site_content(django_apps, type('E', (), {'connection': connection})())
        p = BlogPost.objects.get(slug='old-name')
        self.assertEqual((p.title, p.summary, p.body), ('Why Symaso', 'Symaso saves time', 'Use Symaso.\n\nSecond Symaso line.'))
        self.assertEqual(BlogPost.objects.get(slug='other-name').body, 'Plain text')
        f = FaqItem.objects.get(question='Is Symaso safe?')
        self.assertEqual(f.answer, 'Yes, Symaso is.')
        t = LegalPage.objects.get(key='terms')
        self.assertEqual((t.description, t.body), ('Symaso terms', 'These are the Symaso terms.'))

    def test_running_it_twice_changes_nothing_more(self):
        FaqItem.objects.create(question='MedSync?', answer='a')
        for _ in range(2):
            MIG.rename_in_site_content(django_apps, type('E', (), {'connection': connection})())
        self.assertEqual(FaqItem.objects.get(question='Symaso?').answer, 'a')

    def test_no_visible_old_name_is_left_in_the_code(self):
        root = Path(settings.BASE_DIR)
        hits = []
        for folder in ('templates', 'backend', 'tenants', 'vaultyfly'):
            for f in (root / folder).rglob('*'):
                if f.suffix in ('.py', '.html', '.txt', '.js', '.css') and 'migrations' not in f.parts \
                        and f.name != 'test_rename_symaso.py' and 'MedSync' in f.read_text(errors='ignore'):
                    hits.append(str(f.relative_to(root)))
        self.assertEqual(hits, [])

    def test_home_page_uses_the_new_name(self):
        page = self.client.get('/').content.decode()
        self.assertIn('Symaso', page)
        self.assertNotIn('MedSync', page)
