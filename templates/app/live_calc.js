/* Live preview of a bill or a purchase invoice while it is being typed.
   All money maths is done in whole paise with BigInt and rounded half-to-even, exactly like the server (Python Decimal),
   so the preview shows the same amounts that are saved. It only shows numbers: it never changes any field. */
(function (root) {
  'use strict';
  var ZERO = BigInt(0), ONE = BigInt(1), TWO = BigInt(2), H = BigInt(100), TENK = BigInt(10000);

  // text -> paise (BigInt); '' -> 0; anything that is not a plain number -> null
  function paise(v) {
    var s = String(v == null ? '' : v).trim().replace(/,/g, '');
    if (s === '') return ZERO;
    var m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(s);
    if (!m || (m[2] === '' && !m[3])) return null;
    var frac = m[3] || '';
    var n = BigInt((m[2] || '0') + (frac + '00').slice(0, 2));
    if (frac.length > 2 && frac.charCodeAt(2) >= 53) n += ONE;
    return m[1] ? -n : n;
  }
  function whole(v) {                    // text -> BigInt whole number, or null
    var s = String(v == null ? '' : v).trim();
    return /^\d+$/.test(s) ? BigInt(s) : null;
  }
  function divHalfEven(n, d) {           // n >= 0, d > 0
    var q = n / d, r = n - q * d, t = r * TWO;
    if (t > d || (t === d && q % TWO === ONE)) q += ONE;
    return q;
  }
  function split(gst, igst) {
    if (igst) return {cgst: ZERO, sgst: ZERO, igst: gst};
    var c = divHalfEven(gst, TWO);
    return {cgst: c, sgst: gst - c, igst: ZERO};
  }
  // One bill row. qty: whole number, rate/flat: paise, pct: hundredths of a percent, gstRate: whole percent.
  function billLine(qty, rate, pct, flat, gstRate, igst) {
    var gross = rate * qty;
    var n = gross * (TENK - pct) - flat * TENK;
    var taxable = n > ZERO ? divHalfEven(n, TENK) : ZERO;
    var gst = divHalfEven(taxable * gstRate, H);
    var s = split(gst, igst);
    return {gross: gross, discount: gross - taxable, taxable: taxable, gst: gst, cgst: s.cgst, sgst: s.sgst, igst: s.igst, total: taxable + gst};
  }
  // One purchase row. price: the total paid for the row including GST (paise).
  function purchaseLine(price, gstRate) {
    var tax = divHalfEven(price * H, gstRate + H);
    var gst = price - tax;
    var s = split(gst, false);
    return {taxable: tax, gst: gst, cgst: s.cgst, sgst: s.sgst, total: price};
  }
  function roundRupee(total) { return ((total + BigInt(50)) / H) * H; }       // half up, like the printed invoice
  function money(p) {                    // paise -> '1,23,456.50' (Indian digit grouping)
    var neg = p < ZERO; if (neg) p = -p;
    var s = p.toString(); while (s.length < 3) s = '0' + s;
    var rs = s.slice(0, -2), ps = s.slice(-2), out = '';
    if (rs.length > 3) {
      var rest = rs.slice(0, -3); out = ',' + rs.slice(-3);
      while (rest.length > 2) { out = ',' + rest.slice(-2) + out; rest = rest.slice(0, -2); }
      rs = rest + out;
    }
    return (neg ? '-' : '') + rs + '.' + ps;
  }

  var api = {paise: paise, whole: whole, billLine: billLine, purchaseLine: purchaseLine, roundRupee: roundRupee, money: money, divHalfEven: divHalfEven};
  root.MedCalc = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  // ------------------------------------------------------------------ screen part (only on the bill / purchase form)
  if (typeof document === 'undefined') return;
  var box = document.getElementById('live'); if (!box) return;
  var KIND = box.getAttribute('data-kind');                       // 'bills' or 'purchases'
  var body = document.getElementById('rows'); if (!body) return;
  var linesBody = document.getElementById('live-lines'), sum = document.getElementById('live-sum'), warn = document.getElementById('live-warn');
  var empty = document.getElementById('live-empty'), tableWrap = document.getElementById('live-tw');
  var saved = {}; try { var sv = document.getElementById('saved-gst'); if (sv) saved = JSON.parse(sv.textContent) || {}; } catch (e) { saved = {}; }
  var known = {}, asked = {}, failed = false, lastSig = null;

  function f(tr, name) { return tr.querySelector('[name$="-' + name + '"]'); }
  function val(tr, name) { var e = f(tr, name); return e ? e.value : ''; }
  function setText(el, t) { el.textContent = t; return el; }
  function cell(tr, t, cls) { var td = document.createElement('td'); td.textContent = t; if (cls) td.className = cls; tr.appendChild(td); return td; }

  function readRows() {
    var out = [], trs = body.querySelectorAll('tr');
    for (var i = 0; i < trs.length; i++) {
      var tr = trs[i]; if (tr.classList.contains('hintrow')) continue;
      var sel = f(tr, 'product'); if (!sel || !sel.value) continue;
      var del = f(tr, 'DELETE'); if (del && del.checked) continue;
      var opt = sel.options[sel.selectedIndex];
      out.push({n: out.length + 1, tr: tr, product: sel.value, name: opt ? opt.text : '', id: val(tr, 'id'),
                qty: val(tr, 'qty'), rate: val(tr, 'rate'), pct: val(tr, 'discount_percent'), flat: val(tr, 'discount_amount'),
                price: val(tr, 'purchase_price'), igst: !!(f(tr, 'is_igst') && f(tr, 'is_igst').checked)});
    }
    return out;
  }
  function gstFor(r) {                   // a saved purchase row keeps the GST rate it was saved with (same rule as the server)
    var s = r.id && saved[r.id];
    if (s && String(s.p) === String(r.product)) return BigInt(s.g);
    return (r.product in known) ? BigInt(known[r.product]) : null;
  }
  function ask(ids) {
    ids = ids.filter(function (id) { return !(id in asked); });
    if (!ids.length) return;
    ids.forEach(function (id) { asked[id] = 1; });
    fetch('/api/gst_rates/?ids=' + ids.join(','), {credentials: 'same-origin'})
      .then(function (r) { if (!r.ok) throw new Error('bad'); return r.json(); })
      .then(function (d) { for (var k in d.rates) known[k] = d.rates[k]; failed = false; })
      .catch(function () { ids.forEach(function (id) { delete asked[id]; }); failed = true; })
      .then(function () { draw(true); });
  }
  function sumRow(label, text, cls) {
    var d = document.createElement('div'); if (cls) d.className = cls;
    var a = document.createElement('span'); a.textContent = label; var b = document.createElement('b'); b.textContent = text;
    d.appendChild(a); d.appendChild(b); sum.appendChild(d);
  }
  function note(t) { var p = document.createElement('div'); p.textContent = t; warn.appendChild(p); }

  function draw(noAsk) {
    var rows = readRows(), missing = [];
    rows.forEach(function (r) { if (gstFor(r) === null && missing.indexOf(r.product) < 0) missing.push(r.product); });
    if (missing.length && !noAsk) ask(missing);
    linesBody.textContent = ''; sum.textContent = ''; warn.textContent = '';
    var T = {gross: ZERO, disc: ZERO, taxable: ZERO, cgst: ZERO, sgst: ZERO, igst: ZERO, total: ZERO}, shown = 0, waiting = false;
    rows.forEach(function (r) {
      var g = gstFor(r), tr = document.createElement('tr');
      var qty = whole(r.qty);
      if (KIND === 'bills') {
        var rate = r.rate.trim() === '' ? null : paise(r.rate), pct = paise(r.pct), flat = paise(r.flat);
        if (qty === null || qty === ZERO || rate === null || rate < ZERO || pct === null || flat === null || pct < ZERO || flat < ZERO) {
          if (qty !== null || r.qty.trim() !== '') { if (qty === null || qty === ZERO) note('Row ' + r.n + ' (' + r.name + '): enter a whole quantity above 0.'); else if (rate === null) note('Row ' + r.n + ' (' + r.name + '): rate is empty.'); else note('Row ' + r.n + ' (' + r.name + '): check rate and discount.'); }
          else if (r.qty.trim() === '') note('Row ' + r.n + ' (' + r.name + '): enter the quantity.');
          return;
        }
        if (g === null) { waiting = true; return; }
        if (pct > ZERO && flat > ZERO) note('Row ' + r.n + ' (' + r.name + '): use either Discount % or Flat, not both.');
        if (pct > BigInt(10000)) note('Row ' + r.n + ' (' + r.name + '): discount cannot be more than 100%.');
        var L = billLine(qty, rate, pct, flat, g, r.igst);
        T.gross += L.gross; T.disc += L.discount; T.taxable += L.taxable; T.cgst += L.cgst; T.sgst += L.sgst; T.igst += L.igst; T.total += L.total;
        cell(tr, r.name, 'nm'); cell(tr, qty.toString() + ' × ' + money(rate), 'r'); cell(tr, L.discount > ZERO ? money(L.discount) : '-', 'r');
        cell(tr, money(L.taxable), 'r'); cell(tr, money(L.gst) + ' (' + g.toString() + '%)', 'r'); cell(tr, money(L.total), 'r b');
      } else {
        var price = r.price.trim() === '' ? null : paise(r.price);
        if (qty === null || qty === ZERO || price === null || price < ZERO) {
          note('Row ' + r.n + ' (' + r.name + '): ' + (price === null || price < ZERO ? 'enter the price.' : 'enter a whole quantity above 0.')); return;
        }
        if (g === null) { waiting = true; return; }
        var P = purchaseLine(price, g);
        T.taxable += P.taxable; T.cgst += P.cgst; T.sgst += P.sgst; T.total += P.total;
        cell(tr, r.name, 'nm'); cell(tr, qty.toString(), 'r'); cell(tr, money(price), 'r');
        cell(tr, money(P.taxable), 'r'); cell(tr, money(P.gst) + ' (' + g.toString() + '%)', 'r');
      }
      var tds = tr.children, ths = box.querySelectorAll('thead th');
      for (var q = 0; q < tds.length && q < ths.length; q++) tds[q].setAttribute('data-label', ths[q].textContent);   // labels for the phone layout
      linesBody.appendChild(tr); shown++;
    });
    if (failed) note('Could not load the GST rates. Check the connection; the amounts will appear after your next change.');
    if (waiting && !failed) note('Calculating…');
    empty.style.display = rows.length ? 'none' : ''; tableWrap.style.display = shown ? '' : 'none';
    if (!shown) return;
    if (KIND === 'bills') {
      var grand = roundRupee(T.total), off = grand - T.total;
      sumRow('Sub total', money(T.gross)); if (T.disc > ZERO) sumRow('Discount', '-' + money(T.disc));
      sumRow('Taxable value', money(T.taxable));
      if (T.igst > ZERO) sumRow('IGST', money(T.igst)); if (T.cgst > ZERO || T.sgst > ZERO) { sumRow('CGST', money(T.cgst)); sumRow('SGST', money(T.sgst)); }
      if (off !== ZERO) sumRow('Round off', (off > ZERO ? '+' : '') + money(off));
      sumRow('Grand total', '₹' + money(grand), 'big');
      var pm = document.getElementById('id_payment_mode'), pd = document.getElementById('id_amount_paid');
      if (pm && pm.value === 'CREDIT') {
        var paid = pd ? paise(pd.value) : ZERO; if (paid === null || paid < ZERO) paid = ZERO;
        var due = grand - paid; if (due < ZERO) due = ZERO;
        sumRow('Paid now', money(paid)); sumRow('Credit due', money(due), 'big');
      }
    } else {
      sumRow('Taxable value', money(T.taxable)); sumRow('CGST', money(T.cgst)); sumRow('SGST', money(T.sgst));
      sumRow('Invoice total', '₹' + money(T.total), 'big');
    }
  }

  // The form is filled by several scripts (scan, held bills, auto prices) that do not always raise events, so the
  // screen simply checks a few times a second whether anything changed. It costs next to nothing.
  function signature() {
    var s = [], trs = body.querySelectorAll('tr');
    for (var i = 0; i < trs.length; i++) {
      var tr = trs[i]; if (tr.classList.contains('hintrow')) continue;
      var els = tr.querySelectorAll('input,select');
      for (var j = 0; j < els.length; j++) { var e = els[j]; s.push(e.type === 'checkbox' ? (e.checked ? '1' : '0') : e.value); }
      s.push('|');
    }
    var pm = document.getElementById('id_payment_mode'), pd = document.getElementById('id_amount_paid');
    s.push(pm ? pm.value : '', pd ? pd.value : '');
    return s.join('\u0001');
  }
  function tick() { if (document.hidden) return; var sig = signature(); if (sig !== lastSig) { lastSig = sig; try { draw(false); } catch (e) { /* the preview must never disturb billing */ } } }
  setInterval(tick, 350); tick();
})(typeof window !== 'undefined' ? window : globalThis);
