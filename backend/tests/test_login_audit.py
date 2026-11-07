"""Sign-ins in the Audit Log and the Users page."""
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from backend import auditlog, login_audit
from backend.models import AuditLog


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class LoginAuditTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.owner = self.User.objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.clerk = self.User.objects.create_user('clerk', password='pw-12345')
        AuditLog.objects.all().delete()

    def actions(self, user=None):
        qs = AuditLog.objects.all()
        if user:
            qs = qs.filter(user=user)
        return list(qs.order_by('id').values_list('user', 'action'))

    def test_a_sign_in_and_a_sign_out_are_logged(self):
        self.client.login(username='clerk', password='pw-12345')
        self.client.logout()
        self.assertEqual(self.actions('clerk'), [('clerk', 'LOGIN'), ('clerk', 'LOGOUT')])

    def test_a_failed_sign_in_is_logged_with_the_name_that_was_tried(self):
        self.assertIsNone(authenticate(username='clerk', password='wrong'))
        self.assertEqual(self.actions(), [('clerk', 'LOGIN FAILED')])

    def test_the_login_page_logs_the_ip_address(self):
        self.client.post('/login/', {'username': 'clerk', 'password': 'pw-12345'}, REMOTE_ADDR='10.1.2.3')
        row = AuditLog.objects.filter(action='LOGIN').first()
        self.assertIsNotNone(row)
        self.assertIn('10.1.2.3', row.detail)

    def test_the_password_is_never_written_to_the_log(self):
        authenticate(username='clerk', password='SuperSecret-123')
        self.assertFalse(AuditLog.objects.filter(detail__icontains='SuperSecret').exists())
        self.assertFalse(AuditLog.objects.filter(target__icontains='SuperSecret').exists())

    def test_a_logging_problem_never_blocks_the_sign_in(self):
        with mock.patch.object(AuditLog.objects, 'create', side_effect=RuntimeError('db down')):
            self.assertTrue(self.client.login(username='clerk', password='pw-12345'))


@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
class UserPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser('owner', 'o@example.com', 'pw-12345')
        self.clerk = User.objects.create_user('clerk', password='pw-12345')
        AuditLog.objects.all().delete()

    def test_rows_show_last_login_and_last_action(self):
        self.clerk.last_login = timezone.now()
        self.clerk.save()
        auditlog.record(self.clerk, 'CREATE', 'Purchase', 'INV-1')
        auditlog.record(self.clerk, 'EDIT', 'Bill', 'INV-2')
        auditlog.record(self.clerk, 'LOGOUT', 'Session', '')
        rows = {r['username']: r for r in login_audit.user_rows()}
        c = rows['clerk']
        self.assertEqual(c['role'], 'User')
        self.assertIsNotNone(c['last_login'])
        self.assertIn('EDIT Bill', c['last_action'])          # signing out is not an "action"
        self.assertEqual(c['week'], 2)
        self.assertEqual(rows['owner']['role'], 'Owner')
        self.assertEqual(rows['owner']['last_action'], '')

    def test_failed_sign_ins_are_counted(self):
        authenticate(username='clerk', password='x')
        authenticate(username='clerk', password='y')
        rows = {r['username']: r for r in login_audit.user_rows()}
        self.assertEqual(rows['clerk']['failed'], 2)

    def test_only_the_owner_sees_the_page(self):
        self.client.force_login(self.owner)
        r = self.client.get('/users/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'clerk')
        self.client.force_login(self.clerk)
        self.assertEqual(self.client.get('/users/').status_code, 403)
