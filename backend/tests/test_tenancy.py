"""Rules for many shops that need no database: web address names, trial and plan dates, the notice at the top of a page."""
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from backend import tenancy

TODAY = date(2026, 10, 23)


def shop(**kw):
    base = dict(is_paused=False, plan='trial', trial_ends=date(2026, 11, 22), paid_until=None, schema_name='s_demo')
    base.update(kw)
    return SimpleNamespace(**base)


class SubdomainTests(SimpleTestCase):
    def test_good_names_are_lowercased(self):
        for raw, want in (('sharma', 'sharma'), ('  Sharma-Medical ', 'sharma-medical'), ('a1b', 'a1b'), ('shop2026', 'shop2026')):
            with self.subTest(raw=raw):
                self.assertEqual(tenancy.clean_subdomain(raw), (want, ''))

    def test_bad_names_give_a_message(self):
        for raw in ('', 'ab', 'a' * 31, '-abc', 'abc-', 'a b c', 'abc.def', 'abc_def', 'dükan', 'a--b', 'abc/..'):
            with self.subTest(raw=raw):
                name, msg = tenancy.clean_subdomain(raw)
                self.assertEqual(name, '')
                self.assertTrue(msg)

    def test_reserved_names_are_refused(self):
        for raw in ('www', 'admin', 'API', 'login', 'superadmin', 'medsync'):
            with self.subTest(raw=raw):
                self.assertEqual(tenancy.clean_subdomain(raw)[0], '')

    def test_the_schema_name_is_safe_for_postgresql(self):
        self.assertEqual(tenancy.schema_name_for('sharma-medical'), 's_sharma_medical')
        self.assertEqual(tenancy.schema_name_for('abc'), 's_abc')


class StateTests(SimpleTestCase):
    def test_trial_then_locked(self):
        self.assertEqual(tenancy.shop_state(shop(), TODAY), 'trial')
        self.assertEqual(tenancy.shop_state(shop(trial_ends=TODAY), TODAY), 'trial')        # the last day still counts
        self.assertEqual(tenancy.shop_state(shop(trial_ends=date(2026, 10, 22)), TODAY), 'locked')

    def test_paid_plans(self):
        self.assertEqual(tenancy.shop_state(shop(plan='monthly', paid_until=date(2026, 11, 1)), TODAY), 'active')
        self.assertEqual(tenancy.shop_state(shop(plan='yearly', paid_until=TODAY), TODAY), 'active')
        self.assertEqual(tenancy.shop_state(shop(plan='monthly', paid_until=date(2026, 10, 22)), TODAY), 'locked')
        self.assertEqual(tenancy.shop_state(shop(plan='monthly', paid_until=None), TODAY), 'locked')

    def test_a_paid_date_keeps_a_shop_open_even_after_the_trial(self):
        self.assertEqual(tenancy.shop_state(shop(trial_ends=date(2026, 1, 1), paid_until=date(2026, 12, 1)), TODAY), 'active')

    def test_paused_wins_over_everything(self):
        self.assertEqual(tenancy.shop_state(shop(is_paused=True, paid_until=date(2027, 1, 1)), TODAY), 'paused')

    def test_trial_dates(self):
        self.assertEqual(tenancy.trial_end(TODAY, 30), date(2026, 11, 22))
        with override_settings(TRIAL_DAYS=10):
            self.assertEqual(tenancy.trial_end(TODAY), date(2026, 11, 2))

    def test_days_left(self):
        self.assertEqual(tenancy.days_left(shop(), TODAY), 30)
        self.assertEqual(tenancy.days_left(shop(trial_ends=date(2026, 10, 1)), TODAY), 0)
        self.assertEqual(tenancy.days_left(shop(plan='yearly', paid_until=date(2027, 10, 23)), TODAY), 365)
        self.assertIsNone(tenancy.days_left(shop(trial_ends=None), TODAY))


class NoteTests(SimpleTestCase):
    def note(self, **kw):
        return tenancy.shop_note(SimpleNamespace(tenant=shop(**kw)))

    def test_no_note_without_a_shop_or_on_the_main_site(self):
        self.assertEqual(tenancy.shop_note(SimpleNamespace()), '')
        self.assertEqual(tenancy.shop_note(SimpleNamespace(tenant=shop(schema_name='public', trial_ends=date(2000, 1, 1)))), '')

    def test_trial_note_counts_down(self):
        from django.utils import timezone
        from unittest import mock
        with mock.patch.object(timezone, 'localdate', return_value=TODAY):
            self.assertIn('30 days left', self.note())
            self.assertIn('ends in 3 days', self.note(trial_ends=date(2026, 10, 26)))
            self.assertIn('ends in 1 day.', self.note(trial_ends=date(2026, 10, 24)))
            self.assertIn('subscription has ended', self.note(trial_ends=date(2026, 10, 1)))
            self.assertEqual(self.note(plan='yearly', paid_until=date(2027, 10, 23)), '')
            self.assertIn('Please renew', self.note(plan='monthly', paid_until=date(2026, 10, 25)))

    def test_the_main_site_is_always_the_platform_when_many_shops_is_off(self):
        with override_settings(MULTI_SHOP=False):
            self.assertTrue(tenancy.is_platform())


class TokenTests(SimpleTestCase):
    def test_the_token_is_short_plain_and_different_each_time(self):
        a, b = tenancy.new_token(), tenancy.new_token()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r'^[0-9a-f]{22}$')

    def test_the_confirmation_link_fits_on_one_email_line(self):
        link = f'https://medsync.socialflylive.com/signup/confirm/{tenancy.new_token()}/'
        self.assertLess(len(link), 76)             # email programs break longer lines and the link stops working
