"""Rules for many shops that need no database: web address names, trial and plan dates, the notice at the top of a page."""
from datetime import date, timedelta
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase, override_settings

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


class AddMonthsTests(SimpleTestCase):
    def test_months_are_added_by_the_calendar(self):
        self.assertEqual(tenancy.add_months(date(2026, 10, 23), 1), date(2026, 11, 23))
        self.assertEqual(tenancy.add_months(date(2026, 10, 23), 12), date(2027, 10, 23))
        self.assertEqual(tenancy.add_months(date(2026, 12, 15), 1), date(2027, 1, 15))     # over the year end

    def test_a_short_month_ends_on_its_last_day(self):
        self.assertEqual(tenancy.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(tenancy.add_months(date(2027, 12, 31), 2), date(2028, 2, 29))     # a leap year
        self.assertEqual(tenancy.add_months(date(2028, 2, 29), 12), date(2029, 2, 28))


class SiteFormTests(SimpleTestCase):
    def test_a_good_form_is_cleaned(self):
        values, errors = tenancy.clean_site_form(dict(whatsapp='+91 (98765) 43-210', contact_email=' a@b.com ',
                                                      announcement='  Offer   today ', price_monthly='1,000', price_yearly='9000'))
        self.assertEqual(errors, [])
        self.assertEqual(values, dict(whatsapp='919876543210', contact_email='a@b.com', announcement='Offer today',
                                      shop_notice='', price_monthly=1000, price_yearly=9000,
                                      upi_id='', upi_name='', seller_name='', seller_address='', seller_gstin='', seller_lut='', seller_sac=''))

    def test_blank_fields_hide_things_and_blank_prices_mean_standard(self):
        values, errors = tenancy.clean_site_form({})
        self.assertEqual(errors, [])
        self.assertEqual(values, dict(whatsapp='', contact_email='', announcement='', shop_notice='', price_monthly=None, price_yearly=None,
                                      upi_id='', upi_name='', seller_name='', seller_address='', seller_gstin='', seller_lut='', seller_sac=''))

    def test_the_upi_id_and_name_are_checked(self):
        values, errors = tenancy.clean_site_form(dict(upi_id=' medsync@okhdfcbank ', upi_name='  Med   Sync '))
        self.assertEqual((errors, values['upi_id'], values['upi_name']), ([], 'medsync@okhdfcbank', 'Med Sync'))
        for bad in ('medsync', '@bank', 'a b@bank', 'name@', 'name@1bank', 'x' * 70 + '@okbank', 'a@b@c', 'name@bank&x=1'):
            self.assertTrue(tenancy.clean_site_form(dict(upi_id=bad))[1], bad)
        for bad in ('<b>x</b>', 'a' * 41, 'Med&amp;Sync;'):
            self.assertTrue(tenancy.clean_site_form(dict(upi_name=bad))[1], bad)

    def test_the_seller_details_for_invoices_are_checked(self):
        values, errors = tenancy.clean_site_form(dict(seller_name='  Social   fly ', seller_address=' 1 Road \r\n\r\n Kolkata ', seller_gstin=' 19aabcu9603r1zk ',
                                                      seller_lut=' AD1  ', seller_sac='998314'))
        self.assertEqual(errors, [])
        self.assertEqual((values['seller_name'], values['seller_address'], values['seller_gstin'], values['seller_lut'], values['seller_sac']),
                         ('Social fly', '1 Road\nKolkata', '19AABCU9603R1ZK', 'AD1', '998314'))
        for bad in (dict(seller_gstin='19AABCU9603R1ZJ'), dict(seller_gstin='123'), dict(seller_sac='12'), dict(seller_sac='abcd'), dict(seller_name='x' * 121),
                    dict(seller_address='x' * 301), dict(seller_lut='x' * 41)):
            self.assertTrue(tenancy.clean_site_form(bad)[1], bad)

    def test_bad_entries_give_one_message_each(self):
        _, errors = tenancy.clean_site_form(dict(whatsapp='abc', contact_email='x', announcement='y' * 201,
                                                 price_monthly='50', price_yearly='1e5'))
        self.assertEqual(len(errors), 5)

    def test_prices_must_be_whole_rupees_in_range(self):
        for bad in ('99', '100001', '-500', '700.5', 'free'):
            self.assertTrue(tenancy.clean_site_form(dict(price_monthly=bad))[1], bad)
        for good in ('100', '700', '100000'):
            self.assertFalse(tenancy.clean_site_form(dict(price_monthly=good))[1], good)


class UnknownShopHostTests(SimpleTestCase):
    def test_made_up_shop_addresses_are_recognised(self):
        base = 'medsync.example.com'
        for host in ('nobody.medsync.example.com', 'NOBODY.medsync.example.com:8000', 'a.b.medsync.example.com'):
            self.assertTrue(tenancy.is_unknown_shop_host(host, base), host)

    def test_the_main_site_and_other_names_are_not(self):
        base = 'medsync.example.com'
        for host in ('medsync.example.com', 'medsync.example.com:8000', 'www.medsync.example.com', 'testserver',
                     'example.com', 'evil-medsync.example.com', ''):
            self.assertFalse(tenancy.is_unknown_shop_host(host, base), host)
        self.assertFalse(tenancy.is_unknown_shop_host('anything.localhost', ''))


class ReminderDueTests(SimpleTestCase):
    TODAY = date(2026, 11, 10)

    def shop(self, plan='trial', trial=None, paid=None, paused=False, schema='s_demo'):
        from types import SimpleNamespace
        return SimpleNamespace(schema_name=schema, plan=plan, trial_ends=trial, paid_until=paid, is_paused=paused)

    def due(self, **kw):
        return tenancy.reminder_due(self.shop(**kw), self.TODAY)

    def test_a_trial_gets_a_reminder_a_week_before_and_in_the_last_two_days(self):
        d = lambda n: self.TODAY + timedelta(days=n)
        self.assertIsNone(self.due(trial=d(8)))
        self.assertEqual(self.due(trial=d(7)), ('trial7', d(7)))
        self.assertEqual(self.due(trial=d(2)), ('trial7', d(2)))
        self.assertEqual(self.due(trial=d(1)), ('trial1', d(1)))
        self.assertEqual(self.due(trial=d(0)), ('trial1', d(0)))

    def test_an_ended_trial_gets_one_reminder_for_two_weeks(self):
        d = lambda n: self.TODAY + timedelta(days=n)
        self.assertEqual(self.due(trial=d(-1)), ('trial_end', d(-1)))
        self.assertEqual(self.due(trial=d(-14)), ('trial_end', d(-14)))
        self.assertIsNone(self.due(trial=d(-15)))               # long gone: no more mail

    def test_a_paid_plan_follows_the_same_rules_with_its_own_names(self):
        d = lambda n: self.TODAY + timedelta(days=n)
        self.assertEqual(self.due(plan='monthly', paid=d(5)), ('paid7', d(5)))
        self.assertEqual(self.due(plan='yearly', paid=d(1)), ('paid1', d(1)))
        self.assertEqual(self.due(plan='monthly', paid=d(-3)), ('paid_end', d(-3)))
        self.assertIsNone(self.due(plan='monthly', paid=d(40)))

    def test_paused_shops_the_main_shop_and_shops_without_a_date_get_nothing(self):
        d = self.TODAY + timedelta(days=3)
        self.assertIsNone(self.due(trial=d, paused=True))
        self.assertIsNone(self.due(trial=d, schema='public'))
        self.assertIsNone(self.due(trial=None))
        self.assertIsNone(self.due(plan='monthly', paid=None))

    def test_the_key_changes_with_the_end_date_so_a_renewal_starts_afresh(self):
        a = tenancy.reminder_key('paid7', date(2026, 12, 1))
        b = tenancy.reminder_key('paid7', date(2027, 1, 1))
        self.assertEqual(a, 'paid7:2026-12-01')
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 40)                          # fits the Shop.last_reminder column


class OnShopSiteTests(SimpleTestCase):
    def test_it_is_false_when_many_shops_is_off(self):
        with override_settings(MULTI_SHOP=False):
            self.assertFalse(tenancy.on_shop_site())


class PortSuffixTests(SimpleTestCase):
    def test_a_secure_site_never_shows_the_port_the_app_listens_on(self):
        rf = RequestFactory()
        request = rf.get('/', secure=True, SERVER_NAME='shop.example.com', SERVER_PORT='8091')
        self.assertEqual(tenancy.port_suffix(request), '')

    def test_testing_on_a_computer_keeps_the_port(self):
        rf = RequestFactory()
        self.assertEqual(tenancy.port_suffix(rf.get('/', SERVER_NAME='localhost', SERVER_PORT='8000')), ':8000')
        self.assertEqual(tenancy.port_suffix(rf.get('/', SERVER_NAME='localhost', SERVER_PORT='80')), '')
