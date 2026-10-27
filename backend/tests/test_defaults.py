"""The starting Units and Categories: one list, added without duplicates and without touching what the shop has."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from backend import defaults
from backend.models import Category, Unit


class AddMissingTests(TestCase):
    def test_an_empty_shop_gets_every_unit_and_category(self):
        self.assertEqual(defaults.add_missing(), (len(defaults.UNITS), len(defaults.CATEGORIES)))
        self.assertEqual(set(Unit.objects.values_list('name', flat=True)), set(defaults.UNITS))
        self.assertEqual(set(Category.objects.values_list('name', flat=True)), set(defaults.CATEGORIES))

    def test_running_it_again_adds_nothing(self):
        defaults.add_missing()
        self.assertEqual(defaults.add_missing(), (0, 0))
        self.assertEqual(Unit.objects.count(), len(defaults.UNITS))

    def test_names_are_matched_ignoring_capitals_and_own_entries_stay(self):
        Unit.objects.create(name='strip')
        Category.objects.create(name='My own group')
        units, cats = defaults.add_missing()
        self.assertEqual(units, len(defaults.UNITS) - 1)
        self.assertEqual(Unit.objects.filter(name__iexact='strip').count(), 1)
        self.assertEqual(Unit.objects.get(name__iexact='strip').name, 'strip')
        self.assertTrue(Category.objects.filter(name='My own group').exists())
        self.assertEqual(cats, len(defaults.CATEGORIES))

    def test_the_list_has_no_repeats_and_the_basics(self):
        for names in (defaults.UNITS, defaults.CATEGORIES):
            self.assertEqual(len({n.lower() for n in names}), len(names))
        for basic in ('Tablet', 'Strip', 'Box', 'Bottle'):
            self.assertIn(basic, defaults.UNITS)
        for basic in ('Tablet', 'Syrup', 'Injection'):
            self.assertIn(basic, defaults.CATEGORIES)

    def test_the_reset_uses_the_same_list(self):
        from backend import reset
        self.assertEqual(tuple(reset.DEFAULT_UNITS), tuple(defaults.UNITS))
        self.assertEqual(tuple(reset.DEFAULT_CATEGORIES), tuple(defaults.CATEGORIES))

    def test_seed_master_still_works_and_does_not_repeat(self):
        out = StringIO()
        call_command('seed_master', stdout=out)
        self.assertIn(f'Unit: {len(defaults.UNITS)} added', out.getvalue())
        out = StringIO()
        call_command('seed_master', stdout=out)
        self.assertIn('Unit: 0 added', out.getvalue())
