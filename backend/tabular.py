"""
Small helpers to read and build Excel / CSV files (independent of Django, so they can be tested separately).
"""
import calendar
import csv
import html
import io
import itertools
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_BYTES = 5 * 1024 * 1024


class ImportFileError(Exception):
    """The file itself cannot be read (wrong format, too large, no header found...)."""


# ------------------------------------------------------------------
# Header / cell helpers
# ------------------------------------------------------------------

def norm_header(h):
    h = str(h if h is not None else '').strip().lower().replace('*', '')
    return re.sub(r'[^a-z0-9]+', '_', h).strip('_')


def txt(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float) and v.is_integer():
        v = int(v)                      # so Excel's 9876543210 does not become 9876543210.0
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    s = str(v).strip()
    if len(s) > 1 and s[0] == "'" and s[1] in '=+@-':   # our export added ' to make the cell formula-safe
        s = s[1:]
    return s


def to_dec(v, label, errors, required=False, minimum=None, maximum=None):
    s = txt(v)
    if not s:
        if required:
            errors.append(f"{label} is required")
        return None
    s = s.replace('₹', '').replace(',', '').replace('%', '').replace('Rs.', '').replace('Rs', '').strip()
    try:
        d = Decimal(s)
        if not d.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        errors.append(f"{label}: '{txt(v)}' is not a number")
        return None
    if minimum is not None and d < minimum:
        errors.append(f"{label} cannot be less than {minimum}")
        return None
    if maximum is not None and d > maximum:
        errors.append(f"{label} cannot be more than {maximum}")
        return None
    return d


def to_int(v, label, errors, required=False, minimum=None):
    d = to_dec(v, label, errors, required=required, minimum=minimum)
    if d is None:
        return None
    if d != d.to_integral_value():
        errors.append(f"{label}: '{txt(v)}' must be a whole number (no decimals)")
        return None
    return int(d)


def to_bool(v, label, errors, default=None):
    s = txt(v).lower()
    if not s:
        return default
    if s in ('1', 'yes', 'y', 'true', 'haan', 'ha', 'active'):  # Hindi spellings are still accepted as input
        return True
    if s in ('0', 'no', 'n', 'false', 'nahi', 'inactive'):
        return False
    errors.append(f"{label}: enter 'yes' or 'no'")
    return default


_FULL_DATE = ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y', '%d-%m-%y', '%d/%m/%y', '%d %b %Y', '%d-%b-%Y', '%d-%b-%y')
_MONTH_ONLY = ('%m/%Y', '%m/%y', '%m-%Y', '%m-%y', '%b-%y', '%b-%Y', '%b %Y', '%b %y', '%b/%y', '%b/%Y',
               '%B %Y', '%B-%Y', '%B %y', '%Y-%m')


def to_date(v, label, errors, required=False, month_end=False):
    """
    Date: 2027-08-31, 31/08/2027, 31-08-27 ... Expiry often has only the month (08/27, Aug-27):
    with month_end=True the last day of that month is used (expiry), otherwise the first.
    """
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        s = txt(v)
        if not s:
            if required:
                errors.append(f"{label} is required")
            return None
        d = None
        for fmt in _FULL_DATE:
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                pass
        if d is None:
            for fmt in _MONTH_ONLY:
                try:
                    p = datetime.strptime(s, fmt)
                except ValueError:
                    continue
                d = date(p.year, p.month, calendar.monthrange(p.year, p.month)[1] if month_end else 1)
                break
        if d is None:
            errors.append(f"{label}: '{s}' is not a recognised date (use e.g. 31/08/2027 or 08/27)")
            return None
    if not 2000 <= d.year <= 2100:
        errors.append(f"{label}: the year {d.year} looks wrong")
        return None
    return d


# ------------------------------------------------------------------
# Reading files
# ------------------------------------------------------------------

def read_table(upload, max_rows=3000):
    """
    Reads (headers, rows) from an .xlsx or .csv file.
    headers: list of normalized names. rows: [(sheet_row_number, {header: value}), ...]
    """
    name = (getattr(upload, 'name', '') or '').lower()
    if getattr(upload, 'size', 0) > MAX_BYTES:
        raise ImportFileError('The file is larger than 5 MB. Please split it into smaller files.')

    if name.endswith(('.xlsx', '.xlsm')):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(upload, read_only=True, data_only=True)
            ws = wb.worksheets[0]
            raw = list(itertools.islice(ws.iter_rows(values_only=True), max_rows + 50))
            wb.close()
        except Exception:
            raise ImportFileError('The Excel file could not be read. Please upload the .xlsx file from the template.')
    elif name.endswith('.csv'):
        data = upload.read()
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp1252', errors='replace')
        first = text.splitlines()[0] if text.strip() else ''
        delim = max(',;\t', key=first.count) if first else ','
        raw = list(csv.reader(io.StringIO(text), delimiter=delim))
    elif name.endswith('.xls'):
        raise ImportFileError("Old .xls files are not supported. In Excel use 'Save As' to convert it to .xlsx and upload that.")
    else:
        raise ImportFileError('Please upload an .xlsx or .csv file only.')

    def blank(row):
        return all(txt(c) == '' for c in row)

    start = next((i for i, r in enumerate(raw) if not blank(r)), None)
    if start is None:
        raise ImportFileError('The file is empty.')
    headers = [norm_header(h) for h in raw[start]]
    rows = []
    for i, r in enumerate(raw[start + 1:], start=start + 2):
        if blank(r):
            continue
        rows.append((i, {h: (r[j] if j < len(r) else None) for j, h in enumerate(headers) if h}))
    if not rows:
        raise ImportFileError('No data rows were found below the header.')
    if len(rows) > max_rows:
        raise ImportFileError(f'A maximum of {max_rows} rows can be imported at a time '
                              f'(your file has {len(rows)}). Please split the file into parts.')
    return headers, rows


# ------------------------------------------------------------------
# Building files
# ------------------------------------------------------------------

def safe_cell(v):
    """Protection against Excel formula injection: prefix text starting with =, +, @ (or - followed by letters) with '."""
    if isinstance(v, str) and v and (v[0] in '=+@' or (v[0] == '-' and not re.match(r'-[\d.]', v))):
        return "'" + v
    return v


def plain(v):
    """Turns a list-page HTML cell (badge etc.) into plain text."""
    if v is None:
        return ''
    s = str(v)
    if '<' in s:
        s = re.sub(r'<br\s*/?>', ' ', s)
        s = re.sub(r'<[^>]+>', '', s)
    return html.unescape(s).strip()


def _xl_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        return safe_cell(v)
    return v


def build_xlsx(sheets):
    """
    sheets: [dict(title, headers, rows, required=set(), widths=None, notes=[...])]
    required headers are highlighted, the rest are grey. notes = a simple list of rows like a separate 'Help' sheet.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(sh['title'][:31])
        required = sh.get('required', set())
        ws.append(list(sh['headers']))
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF' if c.value in required else '14302D')
            c.fill = PatternFill('solid', fgColor='0F9D8A' if c.value in required else 'DCEBE8')
            c.alignment = Alignment(vertical='center')
        for row in sh['rows']:
            ws.append([_xl_value(v) for v in row])
        for idx, h in enumerate(sh['headers'], start=1):
            letter = ws.cell(row=1, column=idx).column_letter
            longest = max([len(str(h))] + [len(plain(r[idx - 1])) for r in sh['rows'][:200] if idx - 1 < len(r)])
            ws.column_dimensions[letter].width = min(max(12, longest + 2), 40)
        ws.freeze_panes = 'A2'
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, (date, datetime)):
                    c.number_format = 'DD/MM/YYYY'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_csv(headers, rows):
    out = io.StringIO()
    out.write('﻿')                      # so that Hindi/₹ display correctly in Excel
    w = csv.writer(out)
    w.writerow(headers)
    for r in rows:
        w.writerow([safe_cell(plain(v)) if isinstance(v, str) else ('' if v is None else v) for v in r])
    return out.getvalue()
