"""FAQ, policy pages and home page text of the public website: edited in the admin, shown on the pages, safe, with fallbacks."""
import json
import re
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.test import TestCase, override_settings

from backend import site_seed
from backend.models import FaqItem, HomeText, LegalPage

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'],
            MULTI_SHOP=False)           # these pages are tested as a single-shop site, whatever .env says


def ld_faq(html):
    """The FAQ questions in the page's JSON-LD (parsed, so broken JSON fails the test)."""
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    data = json.loads(block)
    for part in data['@graph']:
        if part['@type'] == 'FAQPage':
            return [(q['name'], q['acceptedAnswer']['text']) for q in part['mainEntity']]
    return []


@override_settings(**SAFE)
class FaqTests(TestCase):
    def test_the_first_questions_were_added_by_the_migration(self):
        self.assertEqual(list(FaqItem.objects.values_list('question', flat=True)), [q for q, _ in site_seed.FAQS])

    def test_home_page_shows_the_questions_and_the_google_markup_matches(self):
        html = self.client.get('/').content.decode()
        for q, a in site_seed.FAQS:
            self.assertIn(f'<summary>{q}</summary>', html)
        self.assertEqual(ld_faq(html), list(site_seed.FAQS))

    def test_an_edited_question_and_its_order(self):
        FaqItem.objects.all().delete()
        FaqItem.objects.create(question='Second?', answer='Two.', position=20)
        FaqItem.objects.create(question='First?', answer='One.', position=10)
        FaqItem.objects.create(question='Hidden?', answer='No.', position=5, active=False)
        html = self.client.get('/').content.decode()
        self.assertLess(html.index('First?'), html.index('Second?'))
        self.assertNotIn('Hidden?', html)
        self.assertEqual(ld_faq(html), [('First?', 'One.'), ('Second?', 'Two.')])

    def test_hiding_every_question_hides_the_box(self):
        FaqItem.objects.update(active=False)
        html = self.client.get('/').content.decode()
        self.assertNotIn('Questions shop owners ask', html)
        self.assertEqual(ld_faq(html), [])
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_with_no_rows_the_standard_questions_are_shown(self):
        FaqItem.objects.all().delete()
        self.assertEqual(len(ld_faq(self.client.get('/').content.decode())), len(site_seed.FAQS))

    def test_html_in_a_question_cannot_run_or_break_the_markup(self):
        FaqItem.objects.all().delete()
        FaqItem.objects.create(question='<script>alert(1)</script>?', answer='</script><b>x</b> "quoted"', position=1)
        html = self.client.get('/').content.decode()
        self.assertNotIn('<summary><script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;?', html)
        self.assertEqual(ld_faq(html), [('<script>alert(1)</script>?', '</script><b>x</b> "quoted"')])   # still valid JSON, tag not closed early


@override_settings(**SAFE)
class HomeTextTests(TestCase):
    def test_standard_text_when_nothing_is_edited(self):
        html = self.client.get('/').content.decode()
        self.assertIn('<h1>Pharmacy billing and GST software for medical stores</h1>', html)
        self.assertIn('MedSync is pharmacy software that bills in seconds', html)
        self.assertIn('See it with your own medicines', html)

    def test_edited_text_is_shown_and_empty_boxes_keep_the_standard(self):
        HomeText.objects.create(hero_title='Billing for your <b>store</b>', final_text='Free for {days} days.')
        html = self.client.get('/').content.decode()
        self.assertIn('<h1>Billing for your &lt;b&gt;store&lt;/b&gt;</h1>', html)
        self.assertIn('MedSync is pharmacy software that bills in seconds', html)      # lead was left empty
        self.assertIn('Free for %d days.' % settings.TRIAL_DAYS, html)
        self.assertIn('See it with your own medicines', html)                           # title was left empty

    def test_only_one_record_can_be_made(self):
        HomeText.objects.create()
        with self.assertRaises(Exception):
            HomeText().full_clean()


@override_settings(**SAFE)
class PolicyPageTests(TestCase):
    def test_the_three_pages_were_added_by_the_migration(self):
        self.assertEqual(set(LegalPage.objects.values_list('key', flat=True)), {'terms', 'privacy', 'refund'})

    def test_pages_show_the_saved_text_with_headings_lists_and_links(self):
        terms = self.client.get('/terms/').content.decode()
        self.assertIn('<h2>1. The service</h2>', terms)
        self.assertIn('<h2>11. Law</h2>', terms)
        self.assertIn('These terms follow the laws of India.', terms)
        self.assertIn('<a href="/privacy/">privacy policy</a>', terms)
        self.assertIn('Last updated:', terms)
        privacy = self.client.get('/privacy/').content.decode()
        self.assertIn('<li>Your account: your name', privacy)
        self.assertIn('class="on"', privacy)                                              # the side menu still works
        self.assertEqual(self.client.get('/refund/').status_code, 200)

    def test_an_edit_and_its_date_are_shown(self):
        page = LegalPage.objects.get(key='refund')
        page.body = '## New rule\n\nWe changed this.'
        page.description = 'My own description.'
        page.updated_on = date(2027, 1, 5)
        page.save()
        html = self.client.get('/refund/').content.decode()
        self.assertIn('<h2>New rule</h2>', html)
        self.assertIn('Last updated: 5 January 2027', html)
        self.assertIn('content="My own description."', html)

    def test_html_in_a_page_is_escaped(self):
        LegalPage.objects.filter(key='terms').update(body='<script>alert(1)</script>\n\n[x](javascript:alert(1))')
        html = self.client.get('/terms/').content.decode()
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('href="javascript', html)

    def test_an_empty_page_falls_back_to_the_built_in_text(self):
        LegalPage.objects.filter(key='terms').update(body='  ')
        html = self.client.get('/terms/').content.decode()
        self.assertIn('Last updated: October 2026', html)
        self.assertIn('<h2>1. The service</h2>', html)

    def test_a_missing_row_falls_back_to_the_built_in_text(self):
        LegalPage.objects.all().delete()
        for key in ('terms', 'privacy', 'refund'):
            self.assertEqual(self.client.get(f'/{key}/').status_code, 200)


@override_settings(**SAFE)
class SiteContentAdminTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345')
        self.client.force_login(self.owner)

    def test_admin_lists_and_forms_open_and_are_in_the_website_section(self):
        home = self.client.get('/admin/').content.decode()
        for name in ('FAQ Questions', 'Policy Pages', 'Home Page Text'):
            self.assertIn(name, home)
        for url in ('/admin/backend/faqitem/', '/admin/backend/faqitem/add/', '/admin/backend/legalpage/',
                    f'/admin/backend/legalpage/{LegalPage.objects.first().pk}/change/', '/admin/backend/hometext/',
                    '/admin/backend/hometext/add/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_a_question_is_added_in_the_admin_and_appears(self):
        r = self.client.post('/admin/backend/faqitem/add/', dict(question='Do you have an app?', answer='Yes, in the browser.', position=5, active='on'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('Do you have an app?', Client().get('/').content.decode())

    def test_policy_pages_cannot_be_added_or_deleted(self):
        self.assertEqual(self.client.get('/admin/backend/legalpage/add/').status_code, 403)
        pk = LegalPage.objects.first().pk
        self.assertEqual(self.client.post(f'/admin/backend/legalpage/{pk}/delete/', {'post': 'yes'}).status_code, 403)
        self.assertTrue(LegalPage.objects.filter(pk=pk).exists())

    def test_the_home_text_can_be_made_once(self):
        r = self.client.post('/admin/backend/hometext/add/', dict(hero_title='New headline', hero_lead='', final_title='', final_text=''))
        self.assertEqual(r.status_code, 302)
        self.assertIn('New headline', Client().get('/').content.decode())            # a visitor who is not logged in
        self.assertEqual(self.client.get('/admin/backend/hometext/add/').status_code, 403)

    def test_a_plain_visitor_cannot_open_them(self):
        self.client.logout()
        for url in ('/admin/backend/faqitem/', '/admin/backend/legalpage/', '/admin/backend/hometext/'):
            self.assertIn(self.client.get(url).status_code, (302, 403), url)
