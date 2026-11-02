// Supplier credit on a purchase return: per line a percent box and a rupee box that follow each other.
// creditInit(form, rowSelector, fullOf(tr), activeOf(tr)): fullOf = value of the returned goods on that line (incl. GST), activeOf = is the line returned.
function creditInit(form, rowSel, fullOf, activeOf) {
  const sumEl = form.querySelector('.credit-sum');
  const r2 = x => Math.round((x + Number.EPSILON) * 100) / 100;
  const money = x => '₹' + x.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const rows = () => [...form.querySelectorAll(rowSel)].filter(tr => tr.querySelector('.cr-pct'));
  const pctOf = tr => { const v = parseFloat(tr.querySelector('.cr-pct').value); return isFinite(v) ? Math.min(Math.max(v, 0), 100) : 100; };
  const fill = tr => { const full = fullOf(tr); tr.querySelector('.cr-amt').value = full > 0 ? r2(full * pctOf(tr) / 100).toFixed(2) : ''; };
  function total() {
    let full = 0, credit = 0;
    rows().forEach(tr => {
      if (!activeOf(tr)) return;
      const f = fullOf(tr), a = parseFloat(tr.querySelector('.cr-amt').value);
      full += f; credit += isFinite(a) ? a : r2(f * pctOf(tr) / 100);
    });
    if (!sumEl) return;
    sumEl.hidden = full <= 0;
    const kept = r2(full - credit);
    sumEl.innerHTML = 'Value of the goods <b>' + money(full) + '</b> · the supplier credits <b>' + money(credit) + '</b>'
      + (kept > 0 ? ' · <b style="color:var(--red)">the supplier keeps ' + money(kept) + '</b>' : '');
  }
  form.addEventListener('input', e => {
    const tr = e.target.closest(rowSel); if (!tr || !tr.querySelector('.cr-pct')) return;
    if (e.target.classList.contains('cr-amt')) {
      const full = fullOf(tr), a = parseFloat(e.target.value);
      if (full > 0 && isFinite(a)) tr.querySelector('.cr-pct').value = String(Math.min(100, r2(a * 100 / full)));
    } else if (e.target.classList.contains('cr-pct') || e.target.hasAttribute('data-credit-trigger')) {
      fill(tr);
    }
    total();
  });
  form.addEventListener('change', e => {
    const tr = e.target.closest(rowSel);
    if (tr && tr.querySelector('.cr-pct') && e.target.classList.contains('cr-amt')) {      // more than the goods are worth: use the full value
      const full = fullOf(tr), a = parseFloat(e.target.value);
      if (full > 0 && isFinite(a) && a > full) { e.target.value = full.toFixed(2); tr.querySelector('.cr-pct').value = '100'; }
    }
    total();
  });
  rows().forEach(tr => { if (!tr.querySelector('.cr-amt').value) fill(tr); });
  total();
}
