// Long drop-downs (thousands of medicines or customers) arrive with only the chosen option. The whole list is fetched once and a
// drop-down is filled just before it is used. Setting .value on such a drop-down adds the option first, so the rest of the page
// can keep doing `select.value = id`. See backend/fastwidgets.py.
(function () {
  'use strict';
  const lists = {}, waiting = {};
  const label = (url, id) => { const l = lists[url]; if (l) for (const o of l) if (o[0] === id) return o[1]; return null; };
  function load(url) {
    if (lists[url]) return Promise.resolve(lists[url]);
    if (!waiting[url]) {
      waiting[url] = fetch(url, {credentials: 'same-origin'})
        .then(r => r.ok ? r.json() : Promise.reject(new Error('list')))
        .then(d => { lists[url] = d.options || []; relabel(url); return lists[url]; })
        .catch(() => { delete waiting[url]; return null; });
    }
    return waiting[url];
  }
  // an option that was added before the list arrived has a stand-in name: give it the real one
  function relabel(url) {
    document.querySelectorAll('select[data-lazy="' + url + '"]').forEach(sel => {
      for (const o of sel.options) { if (o.value && o.dataset.stand) { const t = label(url, o.value); if (t) { o.textContent = t; delete o.dataset.stand; } } }
    });
  }
  function fill(sel) {
    const url = sel.dataset.lazy, list = lists[url];
    if (!list || sel.dataset.lazyDone) return;
    const cur = sel.value, known = new Set(list.map(o => o[0]));
    const blank = [...sel.options].find(o => o.value === ''), extras = [...sel.options].filter(o => o.value && !known.has(o.value));
    const frag = document.createDocumentFragment();
    if (blank) frag.appendChild(new Option(blank.textContent, ''));
    for (const [id, text] of list) frag.appendChild(new Option(text, id));
    extras.forEach(o => frag.appendChild(o));                         // a medicine that was just created, or is not in the list
    sel.textContent = ''; sel.appendChild(frag); sel.value = cur; sel.dataset.lazyDone = '1';
  }
  // select.value = id on a drop-down that does not have that option yet: add it, then set it
  const proto = HTMLSelectElement.prototype, desc = Object.getOwnPropertyDescriptor(proto, 'value');
  Object.defineProperty(proto, 'value', {
    configurable: true, enumerable: desc.enumerable, get: desc.get,
    set(v) {
      if (this.dataset && this.dataset.lazy) {
        const id = String(v);
        if (id !== '' && ![...this.options].some(o => o.value === id)) {
          const known = label(this.dataset.lazy, id), o = new Option(known || ('#' + id), id);
          if (!known) o.dataset.stand = '1';
          this.add(o);
        }
      }
      desc.set.call(this, v);
    }
  });
  function use(e) {
    const sel = e.target;
    if (!sel || !sel.matches || !sel.matches('select[data-lazy]')) return;
    fill(sel);
    load(sel.dataset.lazy).then(() => fill(sel));
  }
  ['mousedown', 'focusin', 'touchstart', 'keydown'].forEach(t => document.addEventListener(t, use, true));
  window.LazySelect = {
    // every product id the drop-down could offer (waits for the list when it is not here yet)
    async ids(sel) {
      if (!sel.dataset || !sel.dataset.lazy) return new Set([...sel.options].map(o => o.value));
      const list = await load(sel.dataset.lazy);
      return new Set([...(list || []).map(o => o[0]), ...[...sel.options].map(o => o.value)]);
    }
  };
  new Set([...document.querySelectorAll('select[data-lazy]')].map(s => s.dataset.lazy)).forEach(load);
})();
