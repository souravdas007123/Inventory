"""The owner is e-mailed when staff do something sensitive; never for the owner, never for ordinary work, never flooded."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings

from backend import auditlog, staff_alerts
from backend.models import AuditLog, ShopProfile

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


@override_settings(**SAFE)
class StaffAlertTests(TestCase):
    def setUp(self):
        staff_alerts.ASYNC = False
        self.addCleanup(setattr, staff_alerts, 'ASYNC', True)
        U = get_user_model()
        self.owner = U.objects.create_superuser('boss', 'boss@example.com', 'pw')
        self.clerk = U.objects.create_user('clerk', 'clerk@example.com', 'pw', is_staff=True)
        shop = ShopProfile.get()
        shop.name = 'City Care'
        shop.save()

    def test_a_staff_delete_e_mails_the_owner(self):
        auditlog.record(self.clerk, 'DELETE', 'Customer', 'Ram Kumar')
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ['boss@example.com'])
        self.assertIn('clerk', m.subject)
        self.assertIn('City Care', m.subject)
        self.assertIn('Ram Kumar', m.body)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_the_shop_e_mail_is_used_too(self):
        shop = ShopProfile.get()
        shop.email = 'shop@example.com'
        shop.save()
        auditlog.record(self.clerk, 'WRITEOFF', 'Batch', 'B1')
        self.assertEqual(sorted(mail.outbox[0].to), ['boss@example.com', 'shop@example.com'])

    def test_editing_a_bill_counts_but_editing_a_product_does_not(self):
        auditlog.record(self.clerk, 'EDIT', 'Product', 'Dolo')
        auditlog.record(self.clerk, 'CREATE', 'Bill', 'B-1')
        auditlog.record(self.clerk, 'LOGIN', 'Session', 'IP 1.2.3.4')
        self.assertEqual(len(mail.outbox), 0)
        auditlog.record(self.clerk, 'EDIT', 'Bill', 'B-1')
        auditlog.record(self.clerk, 'RETURN', 'Sales return', 'CN-1')
        self.assertEqual(len(mail.outbox), 2)

    def test_the_owner_is_never_alerted_about_their_own_actions(self):
        auditlog.record(self.owner, 'DELETE', 'Customer', 'x')
        self.assertEqual(len(mail.outbox), 0)

    def test_a_trusted_staff_owner_role_counts_as_owner(self):
        manager = get_user_model().objects.create_user('mgr', 'mgr@example.com', 'pw', is_staff=True)
        manager.user_permissions.add(Permission.objects.get(codename='change_shopprofile'))
        auditlog.record(manager, 'DELETE', 'Customer', 'x')
        self.assertEqual(len(mail.outbox), 0)

    def test_not_flooded(self):
        for i in range(10):
            auditlog.record(self.clerk, 'DELETE', 'Customer', f'c{i}')
        self.assertEqual(len(mail.outbox), 4)                      # 3 alerts + 1 "making many changes"
        self.assertIn('many changes', mail.outbox[3].subject)
        self.assertEqual(AuditLog.objects.count(), 10)             # the audit log itself has all of them

    def test_failed_logins_and_plain_names_are_ignored(self):
        auditlog.record('somebody', 'DELETE', 'Customer', 'x')
        auditlog.record('(blank)', 'LOGIN FAILED', 'Session', 'IP 1.2.3.4')
        self.assertEqual(len(mail.outbox), 0)

    def test_no_owner_address_means_no_mail_and_no_error(self):
        get_user_model().objects.filter(pk=self.owner.pk).update(email='')
        auditlog.record(self.clerk, 'DELETE', 'Customer', 'x')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_a_broken_mail_server_never_breaks_the_work(self):
        from unittest import mock
        with mock.patch('backend.staff_alerts.send_mail', side_effect=OSError('no network')):
            auditlog.record(self.clerk, 'DELETE', 'Customer', 'x')
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_a_real_delete_through_the_app_alerts(self):
        from django.contrib.auth.models import Permission as P
        self.clerk.user_permissions.add(*P.objects.filter(codename__in=['view_customer', 'delete_customer']))
        from backend.models import Customer
        c = Customer.objects.create(name='Gone Soon')
        self.client.force_login(self.clerk)
        r = self.client.post(f'/customers/{c.pk}/delete/')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Customer.objects.filter(pk=c.pk).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Gone Soon', mail.outbox[0].body)
