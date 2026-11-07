// Live search: a search form marked data-live="region-id" updates that part of the page while the person types,
// so nobody has to press "Search". Without JavaScript (or if anything fails) the form works as before: Enter or the Search button.
(function () {
  if (!window.fetch || !window.DOMParser || !window.URLSearchParams || !window.AbortController) return;
  var form = document.querySelector('form[data-live]');
  var region = form && document.getElementById(form.getAttribute('data-live'));
  if (!form || !region) return;
  var timer = null, ctl = null, seq = 0, lastKey = key();
  function key() { return new URLSearchParams(new FormData(form)).toString(); }
  function target() { var p = new URLSearchParams(new FormData(form)); p.delete('page'); var s = p.toString(); return location.pathname + (s ? '?' + s : ''); }
  function run() {
    var now = key();
    if (now === lastKey) return;
    lastKey = now;
    if (ctl) ctl.abort();
    ctl = new AbortController();
    var mine = ++seq, url = target();
    region.classList.add('live-busy'); region.setAttribute('aria-busy', 'true');
    fetch(url, {credentials: 'same-origin', signal: ctl.signal, headers: {'X-Requested-With': 'live-search'}})
      .then(function (r) {
        if (r.redirected && r.url.indexOf(location.pathname) < 0) { window.location.href = url; return null; }   // signed out: go to the normal page
        return r.ok ? r.text() : null;
      })
      .then(function (html) {
        if (html === null || mine !== seq) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var fresh = doc.getElementById(region.id);
        if (!fresh) return;
        region.replaceChildren.apply(region, [].slice.call(fresh.childNodes).map(function (n) { return document.importNode(n, true); }));
        var links = [].slice.call(document.querySelectorAll('a[href*="export="]')), newer = [].slice.call(doc.querySelectorAll('a[href*="export="]'));
        links.forEach(function (a, i) { if (newer[i]) a.setAttribute('href', newer[i].getAttribute('href')); });   // the download follows the search
        try { history.replaceState(null, '', url); } catch (err) {}
      })
      .catch(function () {})
      .then(function () { if (mine === seq) { region.classList.remove('live-busy'); region.removeAttribute('aria-busy'); } });
  }
  form.addEventListener('input', function (e) {
    if (e.target.tagName === 'SELECT') return;           // a drop-down such as "per page" sends the form itself
    clearTimeout(timer);
    timer = setTimeout(run, e.target.type === 'checkbox' ? 0 : 250);
  });
})();
