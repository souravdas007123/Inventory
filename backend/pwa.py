"""'Add to Home Screen': the web app manifest and a tiny service worker. The worker stores nothing (no private
data is ever kept on the phone); it only shows a friendly page when the phone has no internet."""
import json

from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

OFFLINE_PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>No internet</title><body style="font:16px system-ui;display:grid;place-items:center;min-height:90vh;'
                'margin:0;text-align:center;color:#14302d"><div style="max-width:22em;padding:20px"><h1 style="margin:0 0 8px">'
                'No internet</h1><p>MedSync needs an internet connection. Please check your connection, then try again.</p>'
                '<p><a href="/" style="color:#0f9d8a;font-weight:700">Try again</a></p></div>')

WORKER = """// MedSync service worker: nothing is stored on the device.
const OFFLINE = %s;
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
""" % json.dumps(OFFLINE_PAGE)

MANIFEST = {
    'name': 'MedSync - Pharmacy Inventory and Billing',
    'short_name': 'MedSync',
    'description': 'Pharmacy inventory, GST billing and expiry tracking.',
    'id': '/',
    'start_url': '/',
    'scope': '/',
    'display': 'standalone',
    'orientation': 'any',
    'background_color': '#f1f7f6',
    'theme_color': '#0f9d8a',
    'icons': [
        {'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
        {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
        {'src': '/icon-maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
    ],
}


@require_GET
@cache_control(public=True, max_age=3600)
def manifest(request):
    return HttpResponse(json.dumps(MANIFEST), content_type='application/manifest+json')


@require_GET
@cache_control(no_cache=True)
def service_worker(request):
    resp = HttpResponse(WORKER, content_type='text/javascript; charset=utf-8')
    resp['Service-Worker-Allowed'] = '/'
    return resp
