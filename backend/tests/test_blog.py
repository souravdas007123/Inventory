"""The public blog: rendering is safe, drafts stay hidden, SEO data is present, the seeded articles exist."""
import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from django.core.exceptions import ValidationError

from backend.blog import post_image, reading_minutes, render_body
from backend.blog_art import CHOICES, COVERS, cover_svg
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

    def test_a_picture_line_becomes_a_figure(self):
        html = render_body('![Billing screen](https://example.com/a.png)\n\n![Local](/static/x.png)')
        self.assertIn('<figure><img src="https://example.com/a.png" alt="Billing screen" loading="lazy" decoding="async"></figure>', html)
        self.assertIn('src="/static/x.png"', html)

    def test_unsafe_picture_addresses_are_not_used(self):
        for bad in ('![x](javascript:alert(1))', '![x](http://example.com/a.png)', '![x](//evil.com/a.png)', '![x](data:image/png;base64,AAAA)'):
            self.assertNotIn('<img', render_body(bad), bad)

    def test_a_quote_in_the_picture_text_cannot_break_out(self):
        html = render_body('![a" onerror="alert(1)](https://example.com/a.png)')
        self.assertNotIn('onerror="alert', html)

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
        long = self.make('long-one', summary='word ' * 60)
        page = self.client.get(long.get_absolute_url()).content.decode()
        desc = re.search(r'<meta name="description" content="([^"]*)">', page).group(1)
        self.assertLessEqual(len(desc), 155)           # longer descriptions are cut off in search results
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

    def test_the_home_page_teaser_shows_a_picture_for_each_article(self):
        BlogPost.objects.all().delete()
        BlogPost.objects.create(title='With art', slug='with-art', summary='S.', body='B', published=True, cover='fefo')
        BlogPost.objects.create(title='Own pic', slug='own-pic', summary='S.', body='B', published=True, image_url='https://example.com/p.png')
        html = self.client.get('/').content.decode()
        self.assertEqual(html.count('class="bt"'), 2)
        self.assertIn('<svg', html.split('From the blog')[1])
        self.assertIn('src="https://example.com/p.png"', html)

    def test_every_link_on_the_home_page_has_text_even_with_pictures(self):
        BlogPost.objects.all().delete()
        for i in range(3):
            BlogPost.objects.create(title=f'Article {i}', slug=f'art-{i}', summary='S.', body='B', published=True, cover='fefo')
        html = self.client.get('/').content.decode()
        for attrs, inner in re.findall(r'<a\b([^>]*)>(.*?)</a>', html, re.S):
            self.assertTrue(re.sub(r'<[^>]+>', '', inner).strip() or 'aria-label' in attrs, attrs)

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


class ArtTests(SimpleTestCase):
    def test_every_illustration_is_a_complete_svg(self):
        for key in COVERS:
            svg = cover_svg(key)
            self.assertTrue(svg.startswith('<svg') and svg.endswith('</svg>'), key)
            self.assertIn('role="img"', svg)
            self.assertIn('aria-hidden="true"', cover_svg(key, decorative=True))

    def test_unknown_illustration_gives_nothing(self):
        self.assertEqual(cover_svg('nope'), '')
        self.assertEqual(cover_svg(''), '')

    def test_the_model_choices_match_the_available_illustrations(self):
        self.assertEqual(list(BlogPost._meta.get_field('cover').choices), CHOICES)


@override_settings(**SAFE)
class BlogImageTests(TestCase):
    def make(self, slug='pic', **kw):
        return BlogPost.objects.create(title='Pic post', slug=slug, summary='S.', body='Text.', published=True, **kw)

    def test_the_first_articles_have_an_illustration(self):
        for p in POSTS:
            self.assertEqual(BlogPost.objects.get(slug=p['slug']).cover, p['cover'])
            self.assertIn(p['cover'], COVERS)

    def test_an_article_shows_its_illustration_at_the_top(self):
        self.make(cover='expiry')
        html = self.client.get('/blog/pic/').content.decode()
        self.assertIn('class="hero-img"', html)
        self.assertIn('Illustration: Expiry', html)

    def test_an_own_picture_replaces_the_illustration_and_is_used_for_sharing(self):
        self.make(cover='expiry', image_url='https://example.com/shot.png', image_alt='The billing screen')
        html = self.client.get('/blog/pic/').content.decode()
        self.assertIn('<img src="https://example.com/shot.png" alt="The billing screen"', html)
        self.assertNotIn('Illustration: Expiry', html)
        self.assertIn('<meta property="og:image" content="https://example.com/shot.png">', html)

    def test_a_local_picture_becomes_a_full_address_for_sharing(self):
        self.make(image_url='/static/shot.png')
        self.assertIn('content="http://testserver/static/shot.png"', self.client.get('/blog/pic/').content.decode())

    def test_an_article_without_a_picture_still_works(self):
        self.make()
        resp = self.client.get('/blog/pic/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="hero-img"')
        self.assertContains(resp, 'content="http://testserver/og.png"')

    def test_the_list_shows_a_small_picture_for_each_article(self):
        BlogPost.objects.all().delete()
        self.make('a', cover='gst')
        self.make('b')
        html = self.client.get('/blog/').content.decode()
        self.assertEqual(html.count('class="thumb"'), 1)
        self.assertIn('class="noimg"', html)

    def test_a_bad_picture_address_is_refused_by_the_admin_form(self):
        for bad in ('javascript:alert(1)', 'http://example.com/a.png', '//evil.com/a.png', 'a.png'):
            post = BlogPost(title='T', slug='t', summary='S', body='B', image_url=bad)
            with self.assertRaises(ValidationError, msg=bad):
                post.full_clean()
        BlogPost(title='T', slug='t', summary='S', body='B', image_url='https://example.com/a.png').full_clean()

    def test_a_bad_address_that_got_into_the_database_is_not_shown(self):
        post = self.make(image_url='javascript:alert(1)')
        self.assertNotIn('javascript', post_image(post))
        self.assertNotContains(self.client.get('/blog/pic/'), 'javascript:alert')
