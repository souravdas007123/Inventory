"""The public blog: rendering is safe, drafts stay hidden, SEO data is present, the seeded articles exist."""
import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from backend.blog import reading_minutes, render_body
from backend.blog_seed import POSTS
from backend.models import BlogPost

SAFE = dict(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['testserver'])


class RenderBodyTests(SimpleTestCase):
    def test_headings_lists_bold_and_links(self):
        html = render_body("## Big\n\nText with **bold** and [site](https://example.com).\n\n- one\n- two\n\n1. first\n2. second\n\n### Small")
        self.assertIn('<h2>Big</h2>', html)
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<a href="https://example.com" rel="noopener" target="_blank">site</a>', html)
        self.assertIn('<ul><li>one</li><li>two</li></ul>', html)
        self.assertIn('<ol><li>first</li><li>second</li></ol>', html)
        self.assertIn('<h3>Small</h3>', html)

    def test_html_in_an_article_is_escaped_so_it_cannot_run(self):
        html = render_body('<script>alert(1)</script>\n\n## <img src=x onerror=alert(1)>\n\n- <b>x</b>')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<b>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_only_http_and_site_links_become_links(self):
        html = render_body('[bad](javascript:alert(1)) [ok](/blog/) [also ok](http://example.com)')
        self.assertNotIn('href="javascript', html)
        self.assertIn('<a href="/blog/">ok</a>', html)

    def test_lines_of_one_paragraph_are_joined(self):
        self.assertEqual(render_body('line one\nline two'), '<p>line one line two</p>')

    def test_empty_text_is_fine(self):
        self.assertEqual(render_body(''), '')
        self.assertEqual(render_body(None), '')

    def test_reading_time(self):
        self.assertEqual(reading_minutes('word ' * 450), 2)
        self.assertEqual(reading_minutes(''), 1)


@override_settings(**SAFE)
class BlogPagesTests(TestCase):
    def make(self, slug='a-post', published=True, days=0, **kw):
        return BlogPost.objects.create(title=kw.pop('title', 'A post'), slug=slug, summary=kw.pop('summary', 'Short summary.'),
                                       body=kw.pop('body', '## Part\n\nHello **world**.'), published=published,
                                       published_at=timezone.now() + timedelta(days=days), **kw)

    def test_the_first_articles_were_added_by_the_migration(self):
        for p in POSTS:
            post = BlogPost.objects.get(slug=p['slug'])
            self.assertTrue(post.published)
            self.assertEqual(post.title, p['title'])

    def test_list_shows_published_articles_only(self):
        BlogPost.objects.all().delete()
        self.make('live-one', title='Live article')
        self.make('draft-one', published=False, title='Draft article')
        self.make('future-one', days=3, title='Future article')
        html = self.client.get('/blog/').content.decode()
        self.assertIn('Live article', html)
        self.assertNotIn('Draft article', html)
        self.assertNotIn('Future article', html)

    def test_the_list_says_so_when_there_are_no_articles(self):
        BlogPost.objects.all().delete()
        resp = self.client.get('/blog/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No articles yet')

    def test_an_article_page_has_its_seo_data(self):
        self.make()
        html = self.client.get('/blog/a-post/').content.decode()
        self.assertIn('<title>A post | MedSync</title>', html)
        self.assertIn('<meta name="description" content="Short summary.">', html)
        self.assertIn('<link rel="canonical" href="http://testserver/blog/a-post/">', html)
        self.assertIn('<meta property="og:type" content="article">', html)
        self.assertIn('<strong>world</strong>', html)
        self.assertEqual(html.count('<h1>'), 1)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual((data['@type'], data['headline']), ('BlogPosting', 'A post'))

    def test_a_title_cannot_break_out_of_the_page_or_the_json(self):
        self.make('evil', title='</script><script>alert(1)</script>')
        html = self.client.get('/blog/evil/').content.decode()
        self.assertNotIn('<script>alert(1)', html)
        ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(ld)['headline'], '</script><script>alert(1)</script>')

    def test_drafts_and_future_articles_are_not_found_for_visitors(self):
        self.make('draft-one', published=False)
        self.make('future-one', days=2)
        self.assertEqual(self.client.get('/blog/draft-one/').status_code, 404)
        self.assertEqual(self.client.get('/blog/future-one/').status_code, 404)
        self.assertEqual(self.client.get('/blog/no-such-article/').status_code, 404)

    def test_the_owner_can_preview_a_draft_and_it_is_not_indexed(self):
        self.make('draft-one', published=False)
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345'))
        resp = self.client.get('/blog/draft-one/')
        self.assertContains(resp, 'Preview only')
        self.assertContains(resp, 'noindex')

    def test_staff_without_owner_rights_cannot_preview_a_draft(self):
        self.make('draft-one', published=False)
        self.client.force_login(get_user_model().objects.create_user('clerk', password='pw-12345'))
        self.assertEqual(self.client.get('/blog/draft-one/').status_code, 404)

    def test_more_articles_are_linked_at_the_bottom(self):
        BlogPost.objects.all().delete()
        self.make('one', title='First one')
        self.make('two', title='Second one')
        html = self.client.get('/blog/one/').content.decode()
        self.assertIn('More articles', html)
        self.assertIn('Second one', html)

    def test_sitemap_and_robots_include_the_blog(self):
        self.make('in-map')
        self.make('hidden', published=False)
        sitemap = self.client.get('/sitemap.xml').content.decode()
        self.assertIn('http://testserver/blog/</loc>', sitemap)
        self.assertIn('http://testserver/blog/in-map/</loc>', sitemap)
        self.assertNotIn('/blog/hidden/', sitemap)
        self.assertIn('Allow: /blog/', self.client.get('/robots.txt').content.decode())

    def test_the_home_page_links_to_the_blog_and_shows_the_latest(self):
        BlogPost.objects.all().delete()
        self.make('one', title='Home teaser article')
        html = self.client.get('/').content.decode()
        self.assertIn('href="/blog/"', html)
        self.assertIn('Home teaser article', html)
        self.assertIn('href="/blog/one/"', html)

    def test_the_home_page_still_opens_without_articles(self):
        BlogPost.objects.all().delete()
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'From the blog')

    def test_the_admin_can_open_the_article_pages(self):
        self.client.force_login(get_user_model().objects.create_superuser('owner', 'o@example.com', 'pw-12345'))
        self.assertEqual(self.client.get('/admin/backend/blogpost/').status_code, 200)
        self.assertEqual(self.client.get('/admin/backend/blogpost/add/').status_code, 200)
        self.assertContains(self.client.get('/admin/'), 'Blog Articles')
