from django.contrib.auth import get_user_model
from django.test import TestCase


class SidebarLongNameTests(TestCase):
    def test_a_long_email_is_cut_in_the_side_panel_and_shown_in_full_on_hover(self):
        name = 'a.very.long.shop.owner.name.with.many.parts@a-very-long-company-name-example.co.in'
        get_user_model().objects.create_superuser(name, name, 'pw-12345-xx')
        self.client.login(username=name, password='pw-12345-xx')
        page = self.client.get('/').content.decode()
        self.assertIn(f'<span class="u" title="{name}">', page)         # the full address stays available as a tooltip
        self.assertIn('.side .me .u{min-width:0', page)                    # the rule that cuts it with an ellipsis
        self.assertIn('overflow-x:hidden', page)                           # and the panel never scrolls sideways
