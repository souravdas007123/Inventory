"""A NUL (\\x00) character in a search box must never reach the database.

PostgreSQL rejects it with a DataError; SQLite accepts it, so these tests check that the
text the views receive is already clean (that is what protects PostgreSQL).
"""

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from backend.nulstrip import StripNulMiddleware

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'], MULTI_SHOP=False)


def run(request):
    seen = {}

    def view(r):
        seen['get'] = r.GET.copy()
        seen['post'] = r.POST.copy()
        return HttpResponse('ok')
    StripNulMiddleware(view)(request)
    return seen


class MiddlewareTests(TestCase):
    def test_get_values_are_cleaned(self):
        seen = run(RequestFactory().get('/x/', {'q': 'ab\x00cd', 'multi': ['1\x00', '2']}))
        self.assertEqual(seen['get']['q'], 'abcd')
        self.assertEqual(seen['get'].getlist('multi'), ['1', '2'])

    def test_post_form_is_cleaned(self):
        seen = run(RequestFactory().post('/x/', {'name': 'Pa\x00ra', 'note': 'ok'}))
        self.assertEqual(seen['post']['name'], 'Para')
        self.assertEqual(seen['post']['note'], 'ok')

    def test_clean_input_is_left_untouched(self):
        req = RequestFactory().get('/x/', {'q': 'plain'})
        original = req.GET
        self.assertIs(run(req) and req.GET, original)

    def test_json_body_is_not_parsed(self):
        req = RequestFactory().post('/x/', '{"a": 1}', content_type='application/json')
        self.assertEqual(run(req)['post'], {})


@override_settings(**SAFE)
class PagesNeverSeeNulTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('boss', 'b@x.com', 'pw'))

    def test_search_pages_echo_clean_text(self):
        for url, key in (('/history/', 'customer'), ('/history/', 'doctor'), ('/history/', 'patient'),
                         ('/finder/', 'q'), ('/products/', 'q')):
            r = self.client.get(url, {key: 'ab\x00cd'})
            self.assertEqual(r.status_code, 200, url)
            self.assertNotIn('\x00', r.content.decode(), url)
            if url != '/products/':
                self.assertContains(r, 'abcd')

    def test_queries_sent_to_the_database_have_no_nul(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/history/', {'customer': 'x\x00y', 'doctor': 'd\x00r'})
            self.client.get('/finder/', {'q': 'p\x00q'})
        for q in ctx.captured_queries:
            self.assertNotIn('\x00', q['sql'])
