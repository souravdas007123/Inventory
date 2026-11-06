"""'Add to Home Screen': the web app manifest and a tiny service worker. The worker stores nothing (no private
data is ever kept on the phone); it only shows a friendly page when the phone has no internet."""
import json

from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

OFFLINE_PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>No internet</title><body style="font:16px system-ui;display:grid;place-items:center;min-height:90vh;'
                'margin:0;text-align:center;color:#14302d"><div style="max-width:22em;padding:20px"><h1 style="margin:0 0 8px">'
                'No internet</h1><p>Symaso needs an internet connection. Please check your connection, then try again.</p>'
                '<p><a href="/" style="color:#0f9d8a;font-weight:700">Try again</a></p>'
                '<p style="margin-top:22px;font-size:14px">Billing? <a href="/bills/new/" style="color:#0f9d8a;font-weight:700">Open the New Bill page</a> '
                '(it works offline if you opened it before).</p></div>')

SAVED_PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>Bill saved on this device</title><body style="font:16px system-ui;display:grid;place-items:center;min-height:90vh;'
              'margin:0;text-align:center;color:#14302d"><div style="max-width:24em;padding:20px"><h1 style="margin:0 0 8px">Bill saved on this device</h1>'
              '<p>There is no internet right now. Your bill is kept safely and is sent by itself when the connection is back.</p>'
              '<p><a href="/bills/new/" style="color:#0f9d8a;font-weight:700">Make the next bill</a></p></div>')

WORKER = """// Symaso service worker.
// It keeps (1) the empty New Bill page and the small script files, so billing can go on without internet, and
// (2) a bill whose form could not reach the server, until the page sends it. Nothing else is stored.
importScripts('/static/js/offline-db.js');
const OFFLINE = %s;
const SAVED = %s;
const PAGES = 'medsync-pages-v1', STATIC = 'medsync-static-v1', BILL = '/bills/new/';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== PAGES && k !== STATIC) await caches.delete(k);
  await self.clients.claim();
})()));
const html = (body, status) => new Response(body, {status: status || 200, headers: {'Content-Type': 'text/html; charset=utf-8'}});
async function billPage(req, url) {
  try {
    const res = await fetch(req);
    if (url.search === '' && res.ok && !res.redirected && (res.headers.get('content-type') || '').includes('text/html'))
      (await caches.open(PAGES)).put(BILL, res.clone());          // only the plain page: never one that carries a held bill
    return res;
  } catch (err) {
    const hit = await (await caches.open(PAGES)).match(BILL, {ignoreVary: true, ignoreSearch: true});
    if (hit) return hit;
    if (req.mode === 'navigate') return html(OFFLINE, 503);
    throw err;
  }
}
async function postBill(req) {
  const copy = req.clone();
  try { return await fetch(req); }
  catch (err) {                                   // no connection: keep the bill on this device; the page sends it later
    try {
      const form = await copy.formData(), pairs = [];
      let id = '', name = '', count = 0;
      for (const [k, v] of form.entries()) {
        if (typeof v !== 'string' || k === 'csrfmiddlewaretoken') continue;
        pairs.push([k, v]);
        if (k === 'offline_id') id = v;
        if (k === 'customer_name') name = v;
        if (/^[\\w]+-\\d+-product$/.test(k) && v.trim()) count++;
      }
      if (!id || !count) throw err;               // without its key it could be sent twice, so the browser shows its normal error
      const user = await MSDB.get('meta', 'user');
      await MSDB.put('queue', {id: id, user: user ? user.value : '', created: Date.now(), pairs: pairs, label: name || 'Walk-in',
                               count: count, status: 'waiting', error: '', note: ''});
      return html(SAVED);
    } catch (e2) { throw err; }
  }
}
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.method === 'POST' && url.pathname === BILL && !req.headers.has('X-Offline-Sync')) { e.respondWith(postBill(req)); return; }     // the page's own send is never kept twice
  if (req.method !== 'GET' || req.headers.has('range')) return;
  if (url.pathname === BILL) { e.respondWith(billPage(req, url)); return; }
  if (url.pathname.indexOf('/static/js/') === 0) {                // small scripts: answer from the copy at once, refresh it quietly
    e.respondWith((async () => {
      const cache = await caches.open(STATIC), hit = await cache.match(req);
      const net = fetch(req).then(r => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => null);
      return hit || (await net) || Response.error();
    })());
    return;
  }
  if (req.mode === 'navigate') e.respondWith(fetch(req).catch(() => html(OFFLINE, 503)));
});
self.addEventListener('message', e => {                           // sent when the user signs out: forget the page and the medicine list
  if (e.data === 'clear') e.waitUntil(Promise.all([caches.delete(PAGES), MSDB.clear('meta')]));
});
""" % (json.dumps(OFFLINE_PAGE), json.dumps(SAVED_PAGE))

MANIFEST = {
    'name': 'Symaso - Pharmacy Inventory and Billing',
    'short_name': 'Symaso',
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
