"""Staff and Access: roles, the tick grid, the safety rules."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase, override_settings

from backend import access, staff_access
from backend.models import AuditLog

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)
User = get_user_model()
PW = 'Tr1cky-Pass-9x'


def ticks(**areas):
    """{'billing': 'vac'} -> POST data."""
    letters = {'v': 'view', 'a': 'add', 'c': 'change', 'd': 'delete'}
    return {f'p_{a}_{letters[c]}': '1' for a, ls in areas.items() for c in ls}


class GridRulesTests(TestCase):
    def test_every_area_model_has_its_four_permissions(self):
        for area in staff_access.AREAS:
            for model in area['models']:
                for act in staff_access.ACTIONS:
                    self.assertTrue(Permission.objects.filter(content_type__app_label='backend', codename=f'{act}_{model}').exists(),
                                    f'{act}_{model}')

    def test_nothing_owner_only_can_be_given(self):
        wanted = {m for a in staff_access.AREAS for m in a['models']}
        for forbidden in ('shopprofile', 'auditlog', 'importlayout', 'financialreport', 'twofactor', 'blogpost', 'stockaudit'):
            self.assertNotIn(forbidden, wanted)
        every = staff_access.codenames({a['key']: set(staff_access.ACTIONS) for a in staff_access.AREAS})
        self.assertFalse([c for c in every if c.endswith(('shopprofile', 'user', 'group', 'permission', 'auditlog'))])

    def test_any_action_brings_view_with_it(self):
        g = staff_access.normalise({'billing': {'change'}})
        self.assertEqual(g['billing'], {'view', 'change'})
        self.assertEqual(staff_access.normalise({'billing': {'bogus'}})['billing'], set())
        self.assertEqual(staff_access.normalise({'nope': {'view'}}), staff_access.empty_grid())

    def test_role_names_round_trip(self):
        for key, title, _h, _a in staff_access.ROLES:
            self.assertEqual(staff_access.role_of(staff_access.role_grid(key)), title)
        self.assertEqual(staff_access.role_of(staff_access.empty_grid()), 'No access')
        g = staff_access.role_grid('cashier')
        g['billing'].add('delete')
        self.assertEqual(staff_access.role_of(g), 'Custom')

    def test_roles_never_give_owner_pages(self):
        for key, *_ in staff_access.ROLES:
            u = User.objects.create_user('r-' + key, password=PW)
            staff_access.apply(u, staff_access.role_grid(key))
            u = User.objects.get(pk=u.pk)
            self.assertFalse(access.is_owner(u))
            self.assertFalse(u.is_staff)
            self.assertFalse([p for p in u.get_all_permissions() if not p.startswith('backend.')])


@override_settings(**SAFE)
class StaffPagesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('boss', 'b@x.com', PW)
        self.c = Client()
        self.c.force_login(self.owner)

    def add(self, **extra):
        data = {'full_name': 'Ramesh Kumar', 'username': 'ramesh', 'email': 'r@x.com', 'password': PW, **ticks(billing='vac', customers='vac')}
        data.update(extra)
        return self.c.post('/users/new/', data)

    def test_add_staff_member_gets_exactly_the_ticked_permissions(self):
        r = self.add()
        self.assertRedirects(r, '/users/', fetch_redirect_response=False)
        u = User.objects.get(username='ramesh')
        self.assertEqual((u.first_name, u.last_name), ('Ramesh', 'Kumar'))
        self.assertFalse(u.is_staff or u.is_superuser)
        self.assertTrue(u.is_active and u.check_password(PW))
        self.assertTrue(access.can(u, 'add', 'bill'))
        self.assertTrue(access.can(u, 'view', 'customer'))
        self.assertFalse(access.can(u, 'delete', 'bill'))
        self.assertFalse(access.can(u, 'view', 'purchase'))
        self.assertTrue(AuditLog.objects.filter(action='USER ADDED', detail__startswith='ramesh').exists())

    def test_the_new_staff_member_can_use_the_app_but_not_owner_pages(self):
        self.add()
        staff = Client()
        self.assertTrue(staff.login(username='ramesh', password=PW))
        self.assertEqual(staff.get('/bills/').status_code, 200)
        self.assertEqual(staff.get('/customers/').status_code, 200)
        self.assertEqual(staff.get('/purchases/').status_code, 403)
        for url in ('/users/', '/users/new/', '/report/', '/backup/', '/audit/', '/import/', '/admin/'):
            self.assertIn(staff.get(url).status_code, (302, 403), url)

    def test_view_is_forced_when_another_tick_is_posted(self):
        self.add(**{'p_billing_view': ''}, username='nov')
        u = User.objects.get(username='nov')
        self.assertTrue(u.has_perm('backend.view_bill'))

    def test_bad_input_is_refused_and_shown(self):
        r = self.add(username='bad name!')
        self.assertContains(r, 'errorlist')
        self.assertFalse(User.objects.filter(username='bad name!').exists())
        for pw in ('', '123', 'password', 'ramesh123'):
            r = self.add(password=pw)
            self.assertContains(r, 'errorlist', msg_prefix=pw)
            self.assertFalse(User.objects.filter(username='ramesh').exists(), pw)
        self.assertContains(self.add(email='not-an-email'), 'valid e-mail')

    def test_username_is_unique_ignoring_case(self):
        self.add()
        r = self.add(username='RAMESH')
        self.assertContains(r, 'already used')
        self.assertEqual(User.objects.filter(username__iexact='ramesh').count(), 1)

    def test_a_forged_post_cannot_add_other_permissions(self):
        self.add(**{'p_billing_view': '1', 'permissions': 'backend.change_shopprofile', 'is_superuser': 'on', 'is_staff': 'on',
                    'user_permissions': '1'})
        u = User.objects.get(username='ramesh')
        self.assertFalse(u.is_superuser or u.is_staff)
        self.assertFalse(u.has_perm('backend.change_shopprofile'))

    def test_edit_changes_name_permissions_and_password_only_when_given(self):
        self.add()
        u = User.objects.get(username='ramesh')
        r = self.c.post(f'/users/{u.pk}/', {'full_name': 'Ramesh K', 'email': '', 'password': '', **ticks(stock='vac')})
        self.assertRedirects(r, '/users/', fetch_redirect_response=False)
        u = User.objects.get(pk=u.pk)
        self.assertTrue(u.check_password(PW))                               # empty password: unchanged
        self.assertEqual(u.last_name, 'K')
        self.assertTrue(u.has_perm('backend.add_product'))
        self.assertFalse(u.has_perm('backend.add_bill'))                    # the old ticks are gone
        r = self.c.post(f'/users/{u.pk}/', {'full_name': 'Ramesh K', 'password': 'An0ther-Pass-77', **ticks(stock='v')})
        self.assertTrue(User.objects.get(pk=u.pk).check_password('An0ther-Pass-77'))

    def test_saving_replaces_group_permissions_that_the_screen_cannot_show(self):
        self.add()
        u = User.objects.get(username='ramesh')
        g = Group.objects.create(name='old')
        g.permissions.add(Permission.objects.get(content_type__app_label='backend', codename='change_shopprofile'))
        u.groups.add(g)
        page = self.c.get(f'/users/{u.pk}/')
        self.assertContains(page, 'also has')
        self.c.post(f'/users/{u.pk}/', {'full_name': 'R', **ticks(billing='v')})
        u = User.objects.get(pk=u.pk)
        self.assertFalse(u.groups.exists())
        self.assertFalse(u.has_perm('backend.change_shopprofile'))

    def test_disable_and_enable(self):
        self.add()
        u = User.objects.get(username='ramesh')
        staff = Client()
        staff.login(username='ramesh', password=PW)
        self.assertEqual(staff.get('/bills/').status_code, 200)
        self.c.post(f'/users/{u.pk}/active/')
        self.assertFalse(User.objects.get(pk=u.pk).is_active)
        self.assertEqual(staff.get('/bills/').status_code, 302)             # their open session stops working at once
        self.assertFalse(Client().login(username='ramesh', password=PW))
        self.c.post(f'/users/{u.pk}/active/')
        self.assertTrue(User.objects.get(pk=u.pk).is_active)

    def test_owners_and_yourself_are_not_managed_here(self):
        other = User.objects.create_superuser('boss2', 'b2@x.com', PW)
        for target in (other, self.owner):
            self.assertEqual(self.c.get(f'/users/{target.pk}/').status_code, 403)
            self.assertEqual(self.c.post(f'/users/{target.pk}/', {'full_name': 'x'}).status_code, 403)
            self.assertEqual(self.c.post(f'/users/{target.pk}/active/').status_code, 403)
        self.assertTrue(User.objects.get(pk=other.pk).is_active)
        self.assertEqual(self.c.get('/users/999999/').status_code, 404)

    def test_toggle_needs_post_and_a_staff_member_cannot_use_any_of_it(self):
        self.add()
        u = User.objects.get(username='ramesh')
        self.assertEqual(self.c.get(f'/users/{u.pk}/active/').status_code, 405)
        staff = Client()
        staff.login(username='ramesh', password=PW)
        self.assertEqual(staff.post(f'/users/{u.pk}/active/').status_code, 403)
        self.assertEqual(staff.post('/users/new/', {'username': 'x', 'password': PW}).status_code, 403)
        self.assertFalse(User.objects.filter(username='x').exists())
        self.assertIn(Client().get('/users/new/').status_code, (302, 403))

    def test_list_shows_roles_and_actions(self):
        self.add()
        page = self.c.get('/users/').content.decode()
        self.assertIn('Staff and Access', page)
        self.assertIn('Add staff member', page)
        self.assertIn('/users/%d/' % User.objects.get(username='ramesh').pk, page)
        self.assertIn('Owner', page)
        self.assertNotIn('/users/%d/active/' % self.owner.pk, page)         # no Edit / Disable for the owner

    def test_form_pages_render_for_new_and_edit(self):
        r = self.c.get('/users/new/')
        self.assertContains(r, 'Pick a role')
        for role in ('Cashier', 'Pharmacist', 'Purchase manager', 'Accountant', 'Manager'):
            self.assertContains(r, role)
        self.assertContains(r, 'Only the owner can open')
        self.add()
        u = User.objects.get(username='ramesh')
        page = self.c.get(f'/users/{u.pk}/').content.decode()
        self.assertIn('name="p_billing_add" value="1" data-act="add" aria-label="Billing: Add" checked', page)
        self.assertNotIn('name="p_purchases_view" value="1" data-act="view" aria-label="Purchases and suppliers: View" checked', page)


@override_settings(**SAFE)
class RoleScreenTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('boss', 'b@x.com', PW)
        self.c = Client()
        self.c.force_login(self.owner)

    def test_role_data_for_the_buttons_lists_actions_in_screen_order(self):
        """The buttons compare their ticks with this data in the browser, in the order View, Add, Edit, Delete."""
        import json, re
        page = self.c.get('/users/new/').content.decode()
        data = json.loads(re.search(r'<script id="sf-roles-data" type="application/json">(.*?)</script>', page, re.S).group(1))
        order = list(staff_access.ACTIONS)
        for role in data:
            for acts in role['grid'].values():
                self.assertEqual(acts, [a for a in order if a in acts], role['title'])

    def test_each_role_opens_exactly_its_pages(self):
        pages = {'/bills/': 'billing', '/customers/': 'customers', '/products/': 'stock', '/purchases/': 'purchases',
                 '/expenses/': 'expenses'}
        for key, title, _h, _a in staff_access.ROLES:
            u = User.objects.create_user('u-' + key, password=PW)
            staff_access.apply(u, staff_access.role_grid(key))
            cl = Client()
            cl.force_login(u)
            grid = staff_access.role_grid(key)
            for url, area in pages.items():
                expect = 200 if 'view' in grid[area] else 403
                self.assertEqual(cl.get(url).status_code, expect, f'{title} {url}')
            for url in ('/report/', '/backup/', '/users/', '/audit/', '/import/'):
                self.assertEqual(cl.get(url).status_code, 403, f'{title} {url}')

    def test_phone_layout_keeps_the_area_name_on_the_left(self):
        page = self.c.get('/users/new/').content.decode()
        self.assertIn('table.t.stack.sf-grid td.sf-area{text-align:left!important', page)
