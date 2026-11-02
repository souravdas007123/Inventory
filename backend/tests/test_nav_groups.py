"""The menu: every group is one block (a group split in two shows its heading twice), and the new pages are in the right group."""
from django.test import SimpleTestCase

from backend.views import NAV


class NavGroupTests(SimpleTestCase):
    def test_each_group_is_one_block(self):
        seen, last = [], None
        for n in NAV:
            if n['group'] != last:
                self.assertNotIn(n['group'], seen, f"group {n['group']!r} is split in two (at {n['key']})")
                seen.append(n['group'])
                last = n['group']

    def test_new_pages_are_in_the_right_group_and_unique(self):
        keys = [n['key'] for n in NAV]
        self.assertEqual(len(keys), len(set(keys)))
        by = {n['key']: n for n in NAV}
        self.assertEqual(by['reminders']['group'], 'Customers')
        self.assertEqual(by['history']['group'], 'Customers')
        self.assertEqual(by['finder']['group'], 'Stock')
        self.assertEqual(by['movement']['group'], 'Stock')
        self.assertTrue(by['movement'].get('staff'))         # shows what the stock cost: owner only
