"""Builds the landing page into landing/dist/ after putting your real web address and WhatsApp number in.

1. Fill in site.json:  "site_url": "https://your-domain.in"   "whatsapp": "919876543210" (country code + number, digits only)
2. python landing/build.py
3. Upload everything inside landing/dist/ to your static host (GitHub Pages, Netlify, Cloudflare Pages ...).
"""
import json
import re
import shutil
from datetime import date
from pathlib import Path

here = Path(__file__).parent
cfg = json.loads((here / 'site.json').read_text(encoding='utf-8'))
url = cfg.get('site_url', '').strip().rstrip('/')
wa = re.sub(r'\D', '', cfg.get('whatsapp', ''))
if not re.match(r'^https://[\w.-]+\.[a-z]{2,}(/[\w./-]*)?$', url):
    raise SystemExit('site.json: "site_url" must be your real https address, e.g. https://medsync.in')
if not 10 <= len(wa) <= 15:
    raise SystemExit('site.json: "whatsapp" must be digits with country code, e.g. 919876543210')

out = here / 'dist'
shutil.rmtree(out, ignore_errors=True)
out.mkdir()
for name in ('index.html', '404.html', 'favicon.svg', 'og.png'):
    src = here / name
    if src.exists():
        text = src.read_bytes() if name.endswith('.png') else src.read_text(encoding='utf-8')
        if not isinstance(text, bytes):
            text = text.replace('__SITE_URL__', url).replace('__WHATSAPP__', wa).encode('utf-8')
        (out / name).write_bytes(text)
(out / 'robots.txt').write_text(f'User-agent: *\nAllow: /\n\nSitemap: {url}/sitemap.xml\n', encoding='utf-8')
(out / 'sitemap.xml').write_text(
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    f'  <url><loc>{url}/</loc><lastmod>{date.today().isoformat()}</lastmod><changefreq>monthly</changefreq><priority>1.0</priority></url>\n</urlset>\n',
    encoding='utf-8')
print(f'Built {out} for {url}')
