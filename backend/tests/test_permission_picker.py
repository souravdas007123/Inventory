"""A shop's permission list holds only what a shop uses: no blog, questions, legal pages, home text, social links or Django plumbing.
The main site still sees everything."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings

from backend import permission_picker

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
SITE = ['add_blogpost', 'change_faqitem', 'view_legalpage', 'add_hometext', 'delete_sociallink']
PLUMBING = ['add_logentry', 'add_session', 'view_contenttype', 'change_permission']
SHOP = ['add_product', 'change_purchase', 'view_bill', 'add_customer', 'change_shopprofile']


@override_settings(**SAFE)
class PermissionPickerTests(TestCase):
    def setUp(self):
        self.boss = get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw')
        self.staff = get_user_model().objects.create_user('clerk', password='pw', is_staff=True)
        self.client.force_login(self.boss)

    def pages(self):
        return [self.client.get(f'/admin/auth/user/{self.staff.pk}/change/').content.decode(),
                self.client.get('/admin/auth/group/add/').content.decode()]

    def listed(self, html, codename):
        pk = Permission.objects.get(codename=codename).pk
        return f'<option value="{pk}"' in html

    def in_shop(self, platform):
        return mock.patch('backend.permission_picker.tenancy.is_platform', return_value=platform)

    def test_a_shop_does_not_see_main_site_or_plumbing_permissions(self):
        with self.in_shop(False):
            for html in self.pages():
                for name in SITE + PLUMBING:
                    self.assertFalse(self.listed(html, name), name)
                for name in SHOP:
                    self.assertTrue(self.listed(html, name), name)

    def test_the_main_site_still_sees_everything(self):
        with self.in_shop(True):
            for html in self.pages():
                for name in SITE + SHOP:
                    self.assertTrue(self.listed(html, name), name)

    def test_a_shop_can_still_give_and_save_a_permission(self):
        perm = Permission.objects.get(codename='add_product')
        with self.in_shop(False):
            r = self.client.post('/admin/auth/group/add/', {'name': 'Clerks', 'permissions': [perm.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(Group.objects.get(name='Clerks').permissions.all()), [perm])

    def test_a_hidden_permission_cannot_be_posted_in_a_shop(self):
        perm = Permission.objects.get(codename='add_blogpost')
        with self.in_shop(False):
            r = self.client.post('/admin/auth/group/add/', {'name': 'Sneaky', 'permissions': [perm.pk]})
        self.assertEqual(r.status_code, 200)                  # the form is shown again with an error
        self.assertFalse(Group.objects.filter(name='Sneaky').exists())

    def test_the_list_function_leaves_business_models_alone(self):
        kept = set(permission_picker.shop_permissions(Permission.objects.all()).values_list('codename', flat=True))
        self.assertIn('add_bill', kept)
        self.assertNotIn('add_blogpost', kept)
        self.assertNotIn('add_session', kept)
