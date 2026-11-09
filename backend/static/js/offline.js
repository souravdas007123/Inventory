// Symaso offline billing (page side).
//  * New Bill page: keeps a copy of the medicine list on this device, so medicines can be searched without internet.
//  * A bill made without internet waits on this device (the "queue") and is sent when the internet is back.
//  * Every new bill form carries a one-time key, so a bill can never be saved twice, even if it is sent twice.
//  * A small badge (bottom left) shows the bills that are waiting. It is on every page for people who can bill.
(function () {
  const me = document.currentScript, USER = String((me && me.dataset.user) || ''), db = window.MSDB;
  if (!USER || !db) return;
  const SYNCED_KEEP = 8, CATALOG_FRESH_MS = 15 * 60 * 1000;
  const el = (tag, css, text) => { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text != null) e.textContent = text; return e; };
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); }));
  const cookie = n => (document.cookie.split('; ').find(c => c.indexOf(n + '=') === 0) || '').slice(n.length + 1);
  const ITEM_PRODUCT = /^[\w]+-\d+-product$/;
  const fmtTime = ms => new Date(ms).toLocaleString('en-IN', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'});
  db.put('meta', {key: 'user', value: USER}).catch(() => {});

  // ---------- the queue ----------
  const mine = async () => ((await db.all('queue').catch(() => [])) || []).filter(i => i.user === USER).sort((a, b) => a.created - b.created);
  async function syncedList() { const r = await db.get('meta', 'synced').catch(() => null); return (r && r.list) || []; }
  async function rememberSynced(entry) { const l = await syncedList(); l.unshift(entry); await db.put('meta', {key: 'synced', list: l.slice(0, SYNCED_KEEP)}); }

  let syncing = false;
  async function sendOne(it) {
    const body = new URLSearchParams();
    it.pairs.forEach(p => { if (p[0] !== 'csrfmiddlewaretoken' && p[0] !== 'offline_id') body.append(p[0], p[1]); });
    body.set('offline_id', it.id);
    let r;
    try {
      r = await fetch('/bills/new/', {method: 'POST', body: body, credentials: 'same-origin',
        headers: {'X-CSRFToken': cookie('csrftoken'), 'X-Offline-Sync': '1', 'Accept': 'application/json'}});
    } catch (e) { return 'stop'; }                                              // still no internet: try again later
    if (!(r.headers.get('content-type') || '').includes('application/json')) {
      it.note = r.status === 403 ? 'You are not allowed to make bills with this login.'
        : r.status >= 500 ? 'The server had a problem. It will be tried again.' : 'Please sign in again. Waiting bills are sent after you sign in.';
      await db.put('queue', it);
      return 'stop';
    }
    const d = await r.json().catch(() => null);
    if (d && d.ok) {
      await db.del('queue', it.id);
      await rememberSynced({number: d.number, url: d.url, label: it.label, when: Date.now()});
      toast('Bill ' + d.number + ' saved' + (it.label ? ' (' + it.label + ')' : '') + '.', d.url);
      return 'ok';
    }
    it.status = 'failed'; it.error = ((d && d.errors) || ['The bill could not be saved.']).join(' '); it.note = '';
    await db.put('queue', it);
    return 'failed';
  }
  async function syncAll() {
    if (syncing || navigator.onLine === false) return;
    syncing = true;
    try {
      const run = async () => {
        for (const it of (await mine()).filter(i => i.status !== 'failed')) { if ((await sendOne(it)) === 'stop') break; }
      };
      if (navigator.locks) await navigator.locks.request('medsync-offline-sync', run); else await run();
    } catch (e) { /* the badge shows what is still waiting */ }
    syncing = false;
    refreshBadge();
  }

  // ---------- the badge and its list ----------
  let badge, panel;
  function toast(text, href) {
    const t = el('div', 'position:fixed;left:14px;bottom:62px;z-index:9998;background:#0b2626;color:#fff;border-radius:10px;padding:10px 14px;font:600 14px system-ui;max-width:min(92vw,360px);box-shadow:0 6px 24px rgba(0,0,0,.25)');
    if (href) { const a = el('a', 'color:#7ee7d4;margin-left:8px', 'Open'); a.href = href; t.append(text, a); } else t.textContent = text;
    document.body.appendChild(t); setTimeout(() => t.remove(), 7000);
  }
  async function refreshBadge() {
    const list = await mine(), failed = list.filter(i => i.status === 'failed').length, wait = list.length - failed;
    const offline = navigator.onLine === false, onBill = !!document.getElementById('offline_id');
    if (!list.length && !(offline && onBill)) { if (badge) badge.style.display = 'none'; if (panel) panel.style.display = 'none'; return; }
    if (!badge) {
      badge = el('button', 'position:fixed;left:14px;bottom:14px;z-index:9997;border:0;border-radius:999px;padding:10px 16px;font:700 14px system-ui;color:#fff;cursor:pointer;box-shadow:0 6px 24px rgba(0,0,0,.25)');
      badge.type = 'button'; badge.onclick = () => { panel.style.display = panel.style.display === 'none' ? '' : 'none'; drawPanel(); };
      panel = el('div', 'display:none;position:fixed;left:14px;bottom:62px;z-index:9997;background:#fff;color:#14302d;border:1px solid #cfe1de;border-radius:14px;padding:12px 14px;width:min(94vw,420px);max-height:70vh;overflow:auto;font:14px system-ui;box-shadow:0 10px 36px rgba(0,0,0,.25)');
      document.body.append(badge, panel);
    }
    badge.style.display = '';
    badge.style.background = failed ? '#c0392b' : wait ? '#d98b0c' : '#566';
    setLabel(badge, failed ? '@alert ' + failed + ' bill' + (failed > 1 ? 's' : '') + ' need' + (failed > 1 ? '' : 's') + ' attention'
      : wait ? '@hourglass ' + wait + ' bill' + (wait > 1 ? 's' : '') + ' waiting to be saved' : '@wifi-off Offline: billing still works');
    if (panel.style.display !== 'none') drawPanel();
  }
  async function drawPanel() {
    const list = await mine(), done = await syncedList();
    panel.textContent = '';
    panel.appendChild(el('div', 'font-weight:800;margin-bottom:6px', 'Bills made without internet'));
    if (navigator.onLine === false) panel.appendChild(el('div', 'color:#8a5a00;margin-bottom:6px', 'You are offline. Bills are saved on this device and sent when the internet is back. They get their invoice number when they are sent.'));
    if (!list.length) panel.appendChild(el('div', 'color:#667;margin-bottom:6px', 'No bill is waiting.'));
    list.forEach(it => {
      const row = el('div', 'border-top:1px solid #e3eeec;padding:8px 0');
      row.appendChild(el('div', 'font-weight:700', (it.label || 'Walk-in') + ' · ' + it.count + ' item' + (it.count > 1 ? 's' : '')));
      row.appendChild(el('div', 'color:#667;font-size:12px', fmtTime(it.created)));
      if (it.status === 'failed') row.appendChild(el('div', 'color:#c0392b;margin:4px 0', 'Not saved: ' + it.error));
      else row.appendChild(el('div', 'color:#8a5a00;margin:4px 0', it.note || 'Waiting to be sent'));
      const bar = el('div', 'display:flex;gap:6px;flex-wrap:wrap');
      if (it.status === 'failed') bar.appendChild(btn('Fix and save', () => fix(it)));
      else bar.appendChild(btn('Send now', async () => { await syncAll(); drawPanel(); }));
      bar.appendChild(btn('Discard', async () => {
        const ok = window.appConfirm ? await window.appConfirm('Discard this bill? It was never saved and cannot be brought back.', {yes: 'Discard', danger: true}) : false;
        if (ok) { await db.del('queue', it.id); refreshBadge(); }
      }));
      row.appendChild(bar); panel.appendChild(row);
    });
    if (done.length) {
      panel.appendChild(el('div', 'font-weight:800;margin:10px 0 4px', 'Saved from this device'));
      done.forEach(d => { const a = el('a', 'display:block;color:#0f7a69;padding:2px 0', d.number + ' · ' + (d.label || 'Walk-in') + ' · ' + fmtTime(d.when)); a.href = d.url; panel.appendChild(a); });
    }
  }
  function btn(text, fn) {
    const b = el('button', 'border:1px solid #0f9d8a;background:#fff;color:#0f7a69;border-radius:8px;padding:5px 10px;font:700 13px system-ui;cursor:pointer', text);
    b.type = 'button'; b.onclick = fn; return b;
  }
  // "Fix and save": the bill goes on hold at the server (so it cannot be lost), then opens on the New Bill page to be corrected.
  async function fix(it) {
    try {
      const r = await fetch('/bills/hold/', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': cookie('csrftoken')}, body: JSON.stringify({fields: it.pairs.filter(p => p[0] !== 'offline_id').map(p => /-(is_igst|DELETE)$/.test(p[0]) && p[1] === 'on' ? [p[0], '1'] : p)})});
      const d = await r.json().catch(() => null);
      if (r.ok && d && d.ok) { await db.del('queue', it.id); location.href = '/bills/new/?resume=' + encodeURIComponent(d.id); return; }
      toast((d && d.error) || 'Could not open this bill. Please try again.');
    } catch (e) { toast('No internet. Open it again when you are online.'); }
  }

  // ---------- the New Bill page ----------
  const keyBox = document.getElementById('offline_id');
  if (keyBox) {
    keyBox.value = uuid();
    const form = keyBox.closest('form');
    // a copy of the medicine list, kept on this device
    let catalog = null;
    async function loadCatalog() { if (!catalog) { const r = await db.get('meta', 'catalog').catch(() => null); catalog = (r && r.user === USER) ? r : null; } return catalog; }
    async function refreshCatalog() {
      if (navigator.onLine === false) return;
      const c = await loadCatalog();
      if (c && Date.now() - c.ts < CATALOG_FRESH_MS) return;
      try {
        const r = await fetch('/api/offline_catalog/', {credentials: 'same-origin'});
        if (!r.ok) return;
        const d = await r.json();
        catalog = {key: 'catalog', user: USER, ts: Date.now(), results: d.results || [], truncated: !!d.truncated};
        await db.put('meta', catalog);
        noteUpdate();
      } catch (e) { /* offline: the older copy stays */ }
    }
    async function answer(url) {
      await loadCatalog();
      const u = new URL(url, location.href);
      const json = obj => new Response(JSON.stringify(obj), {status: 200, headers: {'Content-Type': 'application/json'}});
      if (u.pathname === '/api/customer_lookup/') return json({found: false});
      if (u.pathname === '/api/customer_history/') return json({found: false});
      if (u.pathname !== '/api/products/lookup/' || !catalog) return null;
      const id = u.searchParams.get('id'), q = (u.searchParams.get('q') || '').trim(), all = catalog.results;
      if (id) { const p = all.find(x => String(x.id) === id); return p ? json({results: [Object.assign({alts: []}, p)], exact: false}) : json({results: [], exact: false}); }
      if (!q) return json({results: [], exact: false});
      const low = q.toLowerCase();
      const codes = [q]; if (/^\d+$/.test(q)) { if (q.length === 13 && q[0] === '0') codes.push(q.slice(1)); else if (q.length === 12) codes.push('0' + q); }
      const hits = all.filter(p => (p.barcode && codes.includes(p.barcode)) || (p.name || '').toLowerCase().includes(low) || (p.composition || '').toLowerCase().includes(low)).slice(0, 8);
      const hit = hits.find(p => p.barcode && codes.includes(p.barcode)); return json({results: hits, exact: !!hit, exact_id: hit ? hit.id : null});
    }
    const realFetch = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      const url = typeof input === 'string' ? input : input.url;
      if (navigator.onLine === false) { const a = await answer(url); if (a) return a; }
      try { return await realFetch(input, init); }
      catch (e) { const a = await answer(url); if (a) return a; throw e; }
    };
    // a note at the top of the form while offline
    const note = el('div', 'background:#fff4dd;border:1px solid #f0cf8a;color:#6b4a00;border-radius:10px;padding:8px 12px;margin-bottom:10px;font-size:14px;display:none');
    form.parentNode.insertBefore(note, form);
    function noteUpdate() {
      if (navigator.onLine !== false) { note.style.display = 'none'; return; }
      note.style.display = '';
      note.textContent = 'You are offline. Keep billing: each bill is saved on this device and sent when the internet is back. ' +
        (catalog ? 'Medicines and stock shown are from ' + fmtTime(catalog.ts) + '. Stock is checked again when the bill is sent.' : 'The medicine list was not saved on this device yet, so medicines cannot be searched until you are online once.');
    }
    // saving without internet
    form.addEventListener('submit', async e => {
      if (navigator.onLine !== false) return;                                   // online: the normal save
      e.preventDefault();
      const pairs = []; let count = 0, name = '';
      for (const f of form.elements) {
        if (!f.name || f.disabled || f.name === 'csrfmiddlewaretoken' || f.type === 'submit' || f.type === 'button' || f.type === 'file') continue;
        if (f.type === 'checkbox') { if (f.checked) pairs.push([f.name, f.value || 'on']); }
        else if (f.type === 'radio') { if (f.checked) pairs.push([f.name, f.value]); }
        else pairs.push([f.name, f.value]);
        if (ITEM_PRODUCT.test(f.name) && f.value.trim()) count++;
        if (f.name === 'customer_name') name = f.value.trim();
      }
      if (!count) { if (window.appAlert) window.appAlert('Add at least one medicine first.'); return; }
      await db.put('queue', {id: keyBox.value, user: USER, created: Date.now(), pairs: pairs, label: name || 'Walk-in', count: count, status: 'waiting', error: '', note: ''});
      toast('Bill saved on this device. It is sent when the internet is back.');
      refreshBadge();
      if (navigator.serviceWorker && navigator.serviceWorker.controller) location.href = '/bills/new/'; else keyBox.value = uuid();
    });
    window.addEventListener('offline', noteUpdate);
    window.addEventListener('online', noteUpdate);
    loadCatalog().then(() => { noteUpdate(); refreshCatalog(); });
    // let the service worker keep a copy of this page for offline use
    if (navigator.serviceWorker && navigator.onLine !== false) {
      navigator.serviceWorker.ready.then(() => realFetch('/bills/new/', {credentials: 'same-origin'}).catch(() => {}));
    }
  }

  // ---------- every page ----------
  document.addEventListener('submit', e => {                                    // signing out: forget the page copy and the medicine list (waiting bills stay)
    const f = e.target;
    if (f && f.getAttribute && /\/logout\/?$/.test(f.getAttribute('action') || '') && navigator.serviceWorker && navigator.serviceWorker.controller)
      navigator.serviceWorker.controller.postMessage('clear');
  }, true);
  window.addEventListener('online', () => { syncAll(); refreshBadge(); });
  window.addEventListener('offline', refreshBadge);
  window.addEventListener('pageshow', () => { refreshBadge(); syncAll(); });
  refreshBadge(); syncAll();
  setInterval(() => { syncAll(); refreshBadge(); }, 60000);
})();
