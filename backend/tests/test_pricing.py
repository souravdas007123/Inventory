"""Pricing page, legal pages, subscriber renewals, off-server backup email and the server-error alert."""
import gzip
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from backend import backup
from backend.models import Plan, SitePage, Subscriber
from backend.site_pages import rupees

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'])


class RupeeTests(SimpleTestCase):
    def test_indian_grouping(self):
        self.assertEqual(rupees(Decimal('999')), '999')
        self.assertEqual(rupees(Decimal('1500')), '1,500')
        self.assertEqual(rupees(Decimal('125000')), '1,25,000')
        self.assertEqual(rupees(Decimal('12500000')), '1,25,00,000')
        self.assertEqual(rupees(Decimal('1499.50')), '1,499.50')
        self.assertEqual(rupees(None), '')


@override_settings(**SAFE, LANDING_WHATSAPP='919876543210')
class PricingPageTests(TestCase):
    def test_seeded_plans_show_price_on_request_and_are_public(self):
        r = self.client.get('/pricing/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Price on request')
        self.assertContains(r, 'Starter')
        self.assertContains(r, 'rel="canonical"')
        self.assertContains(r, 'application/ld+json')
        self.assertContains(r, 'wa.me/919876543210')

    def test_prices_saving_setup_and_highlight(self):
        Plan.objects.all().delete()
        Plan.objects.create(name='Gold', monthly_price=Decimal('1000'), yearly_price=Decimal('10000'), setup_fee=Decimal('2500'),
                            price_note='GST extra', features='One\nTwo', highlight=True)
        r = self.client.get('/pricing/')
        self.assertContains(r, '₹1,000')
        self.assertContains(r, '₹10,000')
        self.assertContains(r, 'save 17%')
        self.assertContains(r, 'One-time setup: ₹2,500')
        self.assertContains(r, 'GST extra')
        self.assertContains(r, 'Most shops choose this')
        self.assertNotContains(r, 'Price on request')

    def test_inactive_plan_is_hidden_and_empty_state_works(self):
        Plan.objects.update(is_active=False)
        r = self.client.get('/pricing/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Starter')
        self.assertContains(r, 'Plans will be shown here soon')

    def test_without_a_whatsapp_number_the_button_goes_to_login(self):
        with override_settings(LANDING_WHATSAPP=''):
            self.assertNotContains(self.client.get('/pricing/'), 'wa.me')

    def test_plan_text_cannot_inject_html(self):
        Plan.objects.all().delete()
        Plan.objects.create(name='<b>X</b>', features='<script>alert(1)</script>')
        r = self.client.get('/pricing/')
        self.assertNotContains(r, '<script>alert(1)</script>')

    def test_sitemap_and_robots_list_the_new_pages(self):
        sm = self.client.get('/sitemap.xml').content.decode()
        for path in ('/pricing/', '/terms/', '/privacy/', '/refund/'):
            self.assertIn(path, sm)
        robots = self.client.get('/robots.txt').content.decode()
        self.assertIn('Allow: /pricing/', robots)


@override_settings(**SAFE)
class LegalPageTests(TestCase):
    def test_pages_open_without_login(self):
        for path in ('/terms/', '/privacy/', '/refund/'):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertContains(r, 'Last updated')

    def test_edited_text_is_shown_and_html_is_escaped(self):
        SitePage.objects.filter(slug='terms').update(title='My Terms', body='## Hello\n\n<script>x</script>')
        r = self.client.get('/terms/')
        self.assertContains(r, 'My Terms')
        self.assertContains(r, '<h2>Hello</h2>')
        self.assertNotContains(r, '<script>x</script>')

    def test_a_deleted_page_is_a_404_not_a_500(self):
        SitePage.objects.filter(slug='privacy').delete()
        self.assertEqual(self.client.get('/privacy/').status_code, 404)

    def test_pricing_page_still_opens_without_the_included_text(self):
        SitePage.objects.filter(slug='pricing-included').delete()
        self.assertEqual(self.client.get('/pricing/').status_code, 200)


class SubscriberTests(TestCase):
    def sub(self, **kw):
        return Subscriber.objects.create(shop_name='Shop', **kw)

    def test_monthly_renewal_clamps_to_month_end(self):
        with mock.patch('backend.models_extra.timezone.localdate', return_value=date(2026, 1, 1)):
            self.assertEqual(self.sub(renews_on=date(2026, 1, 31)).extended_renewal(), date(2026, 2, 28))

    def test_yearly_renewal_and_leap_day(self):
        with mock.patch('backend.models_extra.timezone.localdate', return_value=date(2027, 1, 1)):
            self.assertEqual(self.sub(billing='YEARLY', renews_on=date(2028, 2, 29)).extended_renewal(), date(2029, 2, 28))

    def test_december_rolls_into_next_year(self):
        with mock.patch('backend.models_extra.timezone.localdate', return_value=date(2026, 1, 1)):
            self.assertEqual(self.sub(renews_on=date(2026, 12, 15)).extended_renewal(), date(2027, 1, 15))

    def test_overdue_renewal_counts_from_today(self):
        with mock.patch('backend.models_extra.timezone.localdate', return_value=date(2026, 6, 10)):
            self.assertEqual(self.sub(renews_on=date(2026, 1, 5)).extended_renewal(), date(2026, 7, 10))

    def test_no_date_counts_from_today_and_days_to_renewal(self):
        s = self.sub()
        self.assertIsNone(s.days_to_renewal())
        s.renews_on = date(2026, 3, 10)
        self.assertEqual(s.days_to_renewal(date(2026, 3, 1)), 9)
        self.assertEqual(s.days_to_renewal(date(2026, 3, 12)), -2)

    def test_yearly_saving_percent(self):
        self.assertEqual(Plan(monthly_price=100, yearly_price=1000).yearly_saving_percent(), 17)
        self.assertEqual(Plan(monthly_price=100, yearly_price=1300).yearly_saving_percent(), 0)
        self.assertEqual(Plan(monthly_price=None, yearly_price=1000).yearly_saving_percent(), 0)


@override_settings(**SAFE)
class AdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345-xyz')
        self.client.force_login(self.user)

    def test_admin_pages_open(self):
        sub = Subscriber.objects.create(shop_name='Shop A', renews_on=date(2020, 1, 1))
        for name, args in (('admin:backend_subscriber_changelist', ()), ('admin:backend_subscriber_add', ()),
                           ('admin:backend_subscriber_change', (sub.pk,)), ('admin:backend_plan_changelist', ()),
                           ('admin:backend_plan_add', ()), ('admin:backend_sitepage_changelist', ()), ('admin:index', ())):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_renewal_filter_and_mark_renewed_action(self):
        sub = Subscriber.objects.create(shop_name='Shop A', status='TRIAL', renews_on=date(2020, 1, 1))
        url = reverse('admin:backend_subscriber_changelist')
        self.assertEqual(self.client.get(url, {'renewal': 'due'}).status_code, 200)
        self.assertEqual(self.client.get(url, {'renewal': 'none'}).status_code, 200)
        r = self.client.post(url, {'action': 'mark_renewed', '_selected_action': [sub.pk]}, follow=True)
        self.assertEqual(r.status_code, 200)
        sub.refresh_from_db()
        self.assertGreater(sub.renews_on, date(2020, 1, 1))
        self.assertEqual(sub.status, 'ACTIVE')


@override_settings(**SAFE)
class BackupEmailTests(TestCase):
    def make_file(self, size=100):
        folder = Path(tempfile.mkdtemp())
        path = folder / 'medsync_20260101_010101.sqlite3.gz'
        with gzip.open(path, 'wb') as f:
            f.write(b'x' * size)
        return path

    def test_not_set_sends_nothing(self):
        with override_settings(BACKUP_EMAIL_TO=[]):
            self.assertIn('not set', backup.email_backup(self.make_file()))
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_the_file_as_an_attachment(self):
        with override_settings(BACKUP_EMAIL_TO=['me@example.com']):
            self.assertIn('emailed', backup.email_backup(self.make_file()))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['me@example.com'])
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    def test_too_big_or_missing_file_never_raises(self):
        with override_settings(BACKUP_EMAIL_TO=['me@example.com']):
            self.assertIn('bigger', backup.email_backup(self.make_file(), max_mb=0))
            self.assertIn('failed', backup.email_backup('/no/such/file.gz'))
        self.assertEqual(len(mail.outbox), 0)


@override_settings(**SAFE, DEBUG=False, ADMINS=[('Admin', 'admin@example.com')])
class ErrorAlertTests(TestCase):
    def test_a_server_error_is_emailed_to_the_admins(self):
        client = Client(raise_request_exception=False)
        with mock.patch('backend.landing.render', side_effect=RuntimeError('boom')):
            r = client.get('/')
        self.assertEqual(r.status_code, 500)
        self.assertTrue(any('admin@example.com' in m.to for m in mail.outbox))
