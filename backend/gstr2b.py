"""GSTR-2B reconciliation: the file from the GST portal (what your suppliers filed) against your purchases.

Why: Input Tax Credit (ITC) can only be claimed for an invoice that shows in GSTR-2B. A bill that is in your books
but not in 2B, or whose tax differs, must be sorted out with the supplier before the return is filed.

Parsing and matching are pure functions (no database, nothing is stored), so they can be tested alone.
- parse_json(bytes): the GSTR-2B JSON from the portal (B2B invoices and credit / debit notes)
- parse_rows(headers, rows): a plain Excel / CSV list with GSTIN, invoice number, date, taxable value and taxes
- reconcile(docs, books, first, last): the result rows and the totals
It reads the supplier's GSTIN and invoice number; it does not check anything on the portal itself."""
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from . import gstin as gstin_check

ZERO = Decimal('0')
TOLERANCE = Decimal('1.00')          # rupees: rounding differences up to this are treated as a match
MAX_DOCS = 20000
LOOKBACK_DAYS = 180                  # an invoice of an earlier month can appear in a later month's 2B

MATCHED, MISMATCH, ONLY_BOOKS, ONLY_2B, NUMBER_DIFF, GSTIN_DIFF = (
    'MATCHED', 'MISMATCH', 'ONLY_BOOKS', 'ONLY_2B', 'NUMBER_DIFF', 'GSTIN_DIFF')
ORDER = [MISMATCH, ONLY_BOOKS, ONLY_2B, NUMBER_DIFF, GSTIN_DIFF, MATCHED]
LABELS = {
    MATCHED: 'Matched',
    MISMATCH: 'Amount differs',
    ONLY_BOOKS: 'In your books, not in GSTR-2B',
    ONLY_2B: 'In GSTR-2B, not in your books',
    NUMBER_DIFF: 'Probably the same bill, invoice number differs',
    GSTIN_DIFF: 'Same invoice number, supplier GSTIN differs',
}


class FileProblem(ValueError):
    """The uploaded file cannot be used; the message is shown to the user."""


# ------------------------------------------------------------------ helpers
def money(v):
    try:
        return Decimal(str(v).replace(',', '').strip() or '0')
    except (InvalidOperation, ValueError):
        return ZERO


def norm_number(s):
    """Invoice numbers are compared without case, spaces, slashes, dashes and leading zeros: INV/2026/001 = inv-2026-1."""
    t = re.sub(r'(?<![0-9])0+(?=[0-9])', '', str(s or '').upper())      # leading zeros of every number part
    return re.sub(r'[^A-Z0-9]', '', t)


def parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or '').strip()
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%y', '%d/%m/%y', '%d.%m.%Y', '%d-%b-%Y', '%d %b %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _doc(kind, gstin, name, number, day, taxable, igst, cgst, sgst, cess, total, itc=True, note=''):
    tax = igst + cgst + sgst
    return dict(kind=kind, gstin=gstin_check.normalise(gstin), name=str(name or '').strip(), number=str(number or '').strip(),
                date=day, taxable=taxable, igst=igst, cgst=cgst, sgst=sgst, cess=cess, tax=tax,
                total=total if total else taxable + tax + cess, itc=itc, note=note)


def _amounts(node):
    """taxable / igst / cgst / sgst / cess of an invoice or note: at the top level, or summed over its 'items'."""
    keys = ('txval', 'igst', 'cgst', 'sgst', 'cess')
    if any(k in node for k in keys):
        return [money(node.get(k)) for k in keys]
    out = [ZERO] * 5
    for it in node.get('items') or []:
        for i, k in enumerate(keys):
            out[i] += money(it.get(k))
    return out


# ------------------------------------------------------------------ JSON from the portal
def parse_json(raw):
    try:
        return _parse_json(raw)
    except (AttributeError, TypeError):
        raise FileProblem('The file could not be read: its invoice section is not in the usual GSTR-2B layout. '
                          'Please upload the original JSON from the GST portal.')


def _parse_json(raw):
    """Returns dict(docs, period, gstin, skipped). Raises FileProblem for a file that is not a GSTR-2B."""
    try:
        data = json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError):
        raise FileProblem('This is not a valid JSON file. Download the GSTR-2B JSON from the GST portal '
                          '(Returns > GSTR-2B > Download) and upload that file.')
    root = data.get('data', data) if isinstance(data, dict) else None
    docdata = root.get('docdata') if isinstance(root, dict) else None
    if not isinstance(docdata, dict):
        raise FileProblem('This does not look like a GSTR-2B JSON file (the invoice section was not found).')
    docs = []
    for sup in docdata.get('b2b') or []:
        for inv in sup.get('inv') or []:
            amt = _amounts(inv)
            reverse = str(inv.get('rev', 'N')).upper() == 'Y'
            itc = str(inv.get('itcavl', 'Y')).upper() != 'N'
            note = ('Reverse charge. ' if reverse else '') + ('' if itc else f"ITC not available: {inv.get('rsn') or 'see portal'}. ")
            docs.append(_doc('INV', sup.get('ctin'), sup.get('trdnm'), inv.get('inum'), parse_date(inv.get('dt')),
                             *amt, money(inv.get('val')), itc=itc, note=note.strip()))
    for sup in docdata.get('cdnr') or []:
        for nt in sup.get('nt') or []:
            amt = _amounts(nt)
            kind = 'CN' if str(nt.get('typ', 'C')).upper().startswith('C') else 'DN'
            itc = str(nt.get('itcavl', 'Y')).upper() != 'N'
            docs.append(_doc(kind, sup.get('ctin'), sup.get('trdnm'), nt.get('ntnum'), parse_date(nt.get('dt')),
                             *amt, money(nt.get('val')), itc=itc))
    if len(docs) > MAX_DOCS:
        raise FileProblem(f'The file has more than {MAX_DOCS} documents, which is more than this page handles.')
    period = root.get('rtnprd') or ''
    first = None
    if re.fullmatch(r'\d{6}', str(period)):
        first = date(int(period[2:]), int(period[:2]), 1) if 1 <= int(period[:2]) <= 12 else None
    skipped = [k for k in ('b2ba', 'cdnra', 'isd', 'impg', 'impgsez') if docdata.get(k)]
    return dict(docs=docs, period=first, gstin=gstin_check.normalise(root.get('gstin')), skipped=skipped)


# ------------------------------------------------------------------ plain Excel / CSV
ALIASES = {
    'gstin': ('gstin', 'supplier_gstin', 'gstin_of_supplier', 'ctin', 'gst_no', 'gstin_no'),
    'name': ('trade_legal_name', 'supplier', 'supplier_name', 'name', 'trdnm'),
    'number': ('invoice_number', 'invoice_no', 'invoice', 'inum', 'bill_no', 'bill_number', 'note_number'),
    'date': ('invoice_date', 'date', 'dt', 'bill_date', 'note_date'),
    'taxable': ('taxable_value', 'taxable', 'txval', 'taxable_amount', 'taxable_value'),
    'igst': ('integrated_tax', 'igst', 'integrated_tax_'),
    'cgst': ('central_tax', 'cgst', 'central_tax_'),
    'sgst': ('state_ut_tax', 'sgst', 'state_tax', 'sgst_utgst', 'state_ut_tax_'),
    'cess': ('cess',),
    'total': ('invoice_value', 'total', 'val', 'invoice_total', 'note_value'),
    'type': ('note_type', 'document_type', 'type'),
    'itc': ('itc_availability', 'itc_available', 'itcavl'),
}


def _pick(headers):
    out = {}
    for field, names in ALIASES.items():
        for h in headers:
            core = re.sub(r'(_rs|_?₹)$', '', h)
            if h in names or core in names:
                out[field] = h
                break
    return out


def parse_rows(headers, rows):
    """headers: normalised names (see tabular.read_table); rows: [(row_number, {header: value})]."""
    cols = _pick(headers)
    missing = [f for f in ('gstin', 'number', 'taxable') if f not in cols]
    if missing:
        names = {'gstin': 'GSTIN of the supplier', 'number': 'Invoice number', 'taxable': 'Taxable value'}
        raise FileProblem('These columns were not found: ' + ', '.join(names[m] for m in missing)
                          + '. For the file downloaded from the portal, use the JSON download instead of Excel.')
    docs = []
    for _n, r in rows:
        def get(f):
            return r.get(cols[f]) if f in cols else None
        number = str(get('number') or '').strip()
        if not number:
            continue
        typ = str(get('type') or '').strip().upper()
        kind = 'CN' if typ in ('C', 'CN') or typ.startswith('CREDIT') else 'DN' if typ in ('D', 'DN') or typ.startswith('DEBIT') else 'INV'
        itc_raw = str(get('itc') or 'Y').strip().upper()
        docs.append(_doc(kind, get('gstin'), get('name'), number, parse_date(get('date')), money(get('taxable')),
                         money(get('igst')), money(get('cgst')), money(get('sgst')), money(get('cess')), money(get('total')),
                         itc=not itc_raw.startswith('N')))
    if not docs:
        raise FileProblem('No invoices were found in the file.')
    if len(docs) > MAX_DOCS:
        raise FileProblem(f'The file has more than {MAX_DOCS} rows, which is more than this page handles.')
    return dict(docs=docs, period=None, gstin='', skipped=[])


# ------------------------------------------------------------------ matching
def _row(status, d=None, b=None, note='', diff_taxable=ZERO, diff_tax=ZERO):
    src = d or b
    return dict(
        status=status, label=LABELS[status],
        supplier=(b['supplier'] if b else d['name']) or (d['name'] if d else ''),
        gstin=(d['gstin'] if d else b['gstin']),
        number_2b=d['number'] if d else '', number_books=b['number'] if b else '',
        date_2b=d['date'] if d else None, date_books=b['date'] if b else None,
        taxable_2b=d['taxable'] if d else None, tax_2b=d['tax'] if d else None,
        taxable_books=b['taxable'] if b else None, tax_books=b['tax'] if b else None,
        diff_taxable=diff_taxable, diff_tax=diff_tax, itc=(d['itc'] if d else True),
        note=(' '.join(x for x in (d['note'] if d else '', note) if x)).strip(), sort=(src['date'] or date.min))


def reconcile(docs, books, first, last):
    """docs: parsed 2B documents. books: [dict(number, date, gstin, supplier, taxable, tax, total)] (purchases of your shop).
    first/last: the month being checked. Returns dict(rows, totals, notes)."""
    lookback = first.toordinal() - LOOKBACK_DAYS
    pool = [b for b in books if b['date'] and lookback <= b['date'].toordinal() <= last.toordinal()]
    by_key, used = {}, set()
    for i, b in enumerate(pool):
        if b['gstin']:
            by_key.setdefault((b['gstin'], norm_number(b['number'])), []).append(i)

    rows = []
    invoices = [d for d in docs if d['kind'] == 'INV']
    notes = [d for d in docs if d['kind'] != 'INV']

    pending = []
    for d in invoices:
        idxs = [i for i in by_key.get((d['gstin'], norm_number(d['number'])), []) if i not in used]
        if not idxs:
            pending.append(d)
            continue
        i = idxs[0]
        used.add(i)
        b = pool[i]
        dt, dx = d['taxable'] - b['taxable'], d['tax'] - b['tax']
        notes_txt = ''
        if d['date'] and b['date'] and (d['date'].year, d['date'].month) != (b['date'].year, b['date'].month):
            notes_txt = f"Dates differ: {d['date']:%d %b %Y} in GSTR-2B, {b['date']:%d %b %Y} in your books."
        if abs(dt) <= TOLERANCE and abs(dx) <= TOLERANCE:
            rows.append(_row(MATCHED, d, b, notes_txt, dt, dx))
        else:
            rows.append(_row(MISMATCH, d, b, notes_txt, dt, dx))

    for d in pending:
        free = [i for i in range(len(pool)) if i not in used]
        same_gstin = [i for i in free if pool[i]['gstin'] == d['gstin']
                      and abs(pool[i]['taxable'] - d['taxable']) <= TOLERANCE and abs(pool[i]['tax'] - d['tax']) <= TOLERANCE]
        same_number = [i for i in free if pool[i]['gstin'] != d['gstin'] and norm_number(pool[i]['number']) == norm_number(d['number'])
                       and abs(pool[i]['taxable'] - d['taxable']) <= TOLERANCE]
        if len(same_gstin) == 1:
            used.add(same_gstin[0])
            b = pool[same_gstin[0]]
            rows.append(_row(NUMBER_DIFF, d, b, f"Your books have it as '{b['number']}'. Correct one of the two numbers."))
        elif len(same_number) == 1:
            used.add(same_number[0])
            b = pool[same_number[0]]
            rows.append(_row(GSTIN_DIFF, d, b, f"Your books have supplier GSTIN {b['gstin'] or '(none)'}. Check the GSTIN of this supplier."))
        else:
            rows.append(_row(ONLY_2B, d, None, 'Enter this purchase, or ask the supplier if it is not yours.'))

    no_gstin = []
    for i, b in enumerate(pool):
        if i in used or not (first <= b['date'] <= last):
            continue
        if not b['gstin']:
            no_gstin.append(b)
            continue
        rows.append(_row(ONLY_BOOKS, None, b, 'Do not claim ITC yet. Ask the supplier to file / correct it; it may still show in a later month.'))

    rows.sort(key=lambda r: (ORDER.index(r['status']), r['sort'], r['number_2b'] or r['number_books']))

    def total(status, field):
        return sum((r[field] or ZERO for r in rows if r['status'] == status), ZERO)

    cn = sum((d['tax'] for d in notes if d['kind'] == 'CN'), ZERO)
    dn = sum((d['tax'] for d in notes if d['kind'] == 'DN'), ZERO)
    totals = dict(
        counts={s: sum(1 for r in rows if r['status'] == s) for s in ORDER},
        itc_matched=total(MATCHED, 'tax_2b'),
        itc_at_risk=ZERO,
        itc_not_in_books=total(ONLY_2B, 'tax_2b'),
        credit_notes=cn, debit_notes=dn, credit_note_count=sum(1 for d in notes if d['kind'] == 'CN'),
        debit_note_count=sum(1 for d in notes if d['kind'] == 'DN'),
        no_gstin=len(no_gstin), no_gstin_tax=sum((b['tax'] for b in no_gstin), ZERO),
        itc_blocked=sum((r['tax_2b'] or ZERO for r in rows if not r['itc'] and r['status'] != ONLY_BOOKS), ZERO),
    )
    # ITC you may not claim yet: tax of purchases that 2B does not show at all, plus the extra tax claimed over 2B
    totals['itc_at_risk'] = total(ONLY_BOOKS, 'tax_books') + sum(
        (r['diff_tax'] * -1 for r in rows if r['status'] == MISMATCH and r['diff_tax'] < 0), ZERO)
    return dict(rows=rows, totals=totals, no_gstin=no_gstin, notes=notes)


# ------------------------------------------------------------------ your purchases, and the Excel for the CA
def books_between(first, last):
    """Your purchases dated first..last (plus the look-back) as plain dicts. Three grouped queries, nothing per row."""
    from datetime import timedelta
    from django.db.models import Sum
    from .models import Purchase
    start = first - timedelta(days=LOOKBACK_DAYS)
    qs = (Purchase.objects.filter(invoice_date__gte=start, invoice_date__lte=last).select_related('supplier')
          .annotate(sum_taxable=Sum('items__tax'), sum_gst=Sum('items__gst'), sum_grand=Sum('items__purchase_price')))
    out = []
    for p in qs:
        out.append(dict(id=p.pk, number=p.invoice_number, date=p.invoice_date, gstin=gstin_check.normalise(p.supplier.gstin),
                        supplier=p.supplier.company, taxable=p.sum_taxable or ZERO, tax=p.sum_gst or ZERO, total=p.sum_grand or ZERO))
    return out


def workbook(result, first, last):
    """The result as an Excel file (bytes) for the CA."""
    from .tabular import build_xlsx
    t = result['totals']
    summary = [
        ['Month checked', f"{first:%B %Y}"],
        ['Matched invoices', t['counts'][MATCHED]], ['ITC of matched invoices (GSTR-2B)', float(t['itc_matched'])],
        ['Amount differs', t['counts'][MISMATCH]],
        ['In your books, not in GSTR-2B', t['counts'][ONLY_BOOKS]], ['Tax at risk (do not claim yet)', float(t['itc_at_risk'])],
        ['In GSTR-2B, not in your books', t['counts'][ONLY_2B]], ['Tax in GSTR-2B not booked', float(t['itc_not_in_books'])],
        ['Invoice number differs', t['counts'][NUMBER_DIFF]], ['Supplier GSTIN differs', t['counts'][GSTIN_DIFF]],
        ['Credit notes in GSTR-2B (tax)', float(t['credit_notes'])], ['Debit notes in GSTR-2B (tax)', float(t['debit_notes'])],
        ['Purchases from suppliers without GSTIN', t['no_gstin']],
    ]
    headers = ['Result', 'Supplier', 'GSTIN', 'Invoice no. (GSTR-2B)', 'Invoice no. (your books)', 'Date (GSTR-2B)',
               'Date (your books)', 'Taxable (GSTR-2B)', 'Tax (GSTR-2B)', 'Taxable (your books)', 'Tax (your books)',
               'Taxable difference', 'Tax difference', 'ITC allowed', 'Note']

    def f(v):
        return float(v) if v is not None else ''
    rows = [[r['label'], r['supplier'], r['gstin'], r['number_2b'], r['number_books'],
             r['date_2b'].strftime('%d-%m-%Y') if r['date_2b'] else '', r['date_books'].strftime('%d-%m-%Y') if r['date_books'] else '',
             f(r['taxable_2b']), f(r['tax_2b']), f(r['taxable_books']), f(r['tax_books']),
             f(r['diff_taxable']) if r['status'] in (MATCHED, MISMATCH) else '', f(r['diff_tax']) if r['status'] in (MATCHED, MISMATCH) else '',
             'Yes' if r['itc'] else 'No', r['note']] for r in result['rows']]
    return build_xlsx([dict(title='Summary', headers=['Item', 'Value'], rows=summary),
                       dict(title='Reconciliation', headers=headers, rows=rows)])
