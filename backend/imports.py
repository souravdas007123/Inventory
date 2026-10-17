"""
Bulk import: Products, Customers, Suppliers, Stock-in (Purchases).

Flow (the same for every importer):
  1. validate()  -> checks every row of the file, writes nothing to the DB (for the preview)
  2. stash()     -> cleaned rows saved to a temp file
  3. commit()    -> only valid rows are saved (each row/invoice in its own transaction, via the model's save(),
                    so stock / batch / supplier balance / ledger are all produced correctly)
"""
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone

from .models import (
    Brand, Category, Customer, Expense, Product, ProductUnit, Purchase, PurchaseItem, Supplier, Unit,
)
from .tabular import build_csv, build_xlsx, plain, to_bool, to_date, to_dec, to_int, txt

# Excel files often use short names. This maps them to the correct Unit.
UNIT_ALIASES = {
    'pcs': 'Piece', 'pc': 'Piece', 'pieces': 'Piece', 'nos': 'Piece', 'no': 'Piece',
    'tab': 'Tablet', 'tabs': 'Tablet', 'tablets': 'Tablet',
    'cap': 'Capsule', 'caps': 'Capsule', 'capsules': 'Capsule',
    'strips': 'Strip', 'stp': 'Strip',
    'btl': 'Bottle', 'bottles': 'Bottle',
    'boxes': 'Box', 'bx': 'Box',
    'pkt': 'Pack', 'packs': 'Pack', 'packet': 'Pack',
    'tubes': 'Tube', 'vials': 'Vial', 'sachets': 'Sachet',
    'ml': 'ML', 'mls': 'ML',
}

MAX_ROWS = 3000
GST_ALLOWED = (Decimal('0'), Decimal('5'), Decimal('12'), Decimal('18'))
SCHEDULE_MAP = {
    '': None, 'none': 'NONE', 'normal': 'NONE', 'no': 'NONE', '-': 'NONE', 'otc': 'NONE',
    'h': 'H', 'schedule h': 'H', 'sch h': 'H',
    'h1': 'H1', 'schedule h1': 'H1', 'sch h1': 'H1',
    'x': 'X', 'schedule x': 'X', 'sch x': 'X',
}


def _col(name, required=False, help='', s1='', s2='', need=''):
    """`need` = a short note for a column that is optional in general but needed in some case (shown instead of "Optional")."""
    return dict(name=name, required=required, help=help, samples=(s1, s2), need=need)


def _unit_name(raw):
    s = txt(raw)
    return UNIT_ALIASES.get(s.lower(), s) if s else ''


def _digits(phone):
    return re.sub(r'\D', '', phone or '')[-10:]


def _result(n, action, data=None, errors=None, warnings=None, label=''):
    return dict(n=n, action=action, data=data or {}, errors=errors or [], warnings=warnings or [], label=label)


def _fk(model, name):
    """Get a Category/Brand/Unit by name, creating it if missing (iexact)."""
    name = (name or '').strip()
    if not name:
        return None
    return model.objects.filter(name__iexact=name).first() or model.objects.create(name=name)


# ======================================================
# PRODUCTS
# ======================================================

PRODUCT_COLS = [
    _col('name', True, 'Medicine name', 'Dolo 650', 'Azithral 500'),
    _col('unit', True, 'Smallest unit (stock is counted in this): Tablet, Capsule, Bottle, ML...', 'Tablet', 'Tablet'),
    _col('gst_rate', True, 'GST %: only 0, 5, 12 or 18', '12', '12'),
    _col('composition', False, 'Salt / composition', 'Paracetamol 650mg', 'Azithromycin 500mg'),
    _col('hsn_code', False, 'HSN code', '3004', '3004'),
    _col('category', False, 'Category (created if it is new)', 'Tablet', 'Tablet'),
    _col('brand', False, 'Brand / company (created if it is new)', 'Micro Labs', 'Alembic'),
    _col('mrp', False, 'MRP incl. GST of 1 base unit (billing works out its rate from this)', '2.10', '19.50'),
    _col('barcode', False, 'Barcode (unique for each product)', '8901234567890', ''),
    _col('schedule', False, 'Drug schedule: blank/NONE, H, H1 or X', '', 'H'),
    _col('reorder_level', False, 'Starting reorder level of a NEW product (in base unit, blank = 5). It is calculated automatically from sales afterwards, so it is ignored for existing products', '100', '50'),
    _col('rack', False, 'Rack', 'A1', 'A2'),
    _col('row', False, 'Row', '2', '1'),
    _col('location', False, 'Godown / location', 'Counter', 'Counter'),
    _col('default_supplier', False, 'Supplier name (must already exist in Suppliers)', '', ''),
    _col('pack1_unit', False, 'Larger pack, e.g. Strip', 'Strip', 'Strip'),
    _col('pack1_factor', False, 'Base units in 1 pack1 (e.g. 15)', '15', '5'),
    _col('pack2_unit', False, 'Second larger pack, e.g. Box', 'Box', ''),
    _col('pack2_factor', False, 'Base units in 1 pack2 (e.g. 150)', '150', ''),
    _col('is_active', False, 'yes / no (blank = yes)', 'yes', 'yes'),
]
PRODUCT_ALIASES = {
    'product': 'name', 'product_name': 'name', 'medicine': 'name', 'medicine_name': 'name', 'item': 'name',
    'item_name': 'name', 'base_unit': 'unit', 'gst': 'gst_rate', 'hsn': 'hsn_code',
    'salt': 'composition', 'sale_rate': 'mrp', 'rate': 'mrp', 'drug_schedule': 'schedule',
    'reorder': 'reorder_level', 'supplier': 'default_supplier', 'godown': 'location',
}


def validate_products(rows, opts):
    update = bool(opts.get('update'))
    by_barcode, by_name = {}, {}
    for p in Product.objects.select_related('unit'):
        if p.barcode:
            by_barcode[p.barcode] = p
        by_name.setdefault((p.name or '').strip().lower(), p)
    suppliers = {s.company.strip().lower(): s for s in Supplier.objects.all()}

    results, seen_name, seen_code = [], {}, {}
    for n, raw in rows:
        errors, warnings = [], []
        name = txt(raw.get('name'))
        if not name:
            errors.append('name is required')
        barcode = txt(raw.get('barcode')) or None
        if barcode and len(barcode) > 64:
            errors.append('barcode is longer than 64 characters')

        pb = by_barcode.get(barcode) if barcode else None
        pn = by_name.get(name.lower()) if name else None
        if pb and pn and pb.pk != pn.pk:
            errors.append(f"barcode '{barcode}' belongs to another product ('{pb.name}')")
        if pb and not pn and name and (pb.name or '').strip().lower() != name.lower():
            errors.append(f"barcode '{barcode}' belongs to '{pb.name}' in the system, but the name in the file is different. Please check the name/barcode")
        existing = pb or pn

        if name and name.lower() in seen_name:
            errors.append(f"a row with this name already appears at row {seen_name[name.lower()]} (duplicate in file)")
        if barcode and barcode in seen_code:
            errors.append(f"barcode '{barcode}' also appears in row {seen_code[barcode]}")
        if name:
            seen_name.setdefault(name.lower(), n)
        if barcode:
            seen_code.setdefault(barcode, n)

        unit = _unit_name(raw.get('unit'))
        if not unit and not existing:
            errors.append('unit is required (e.g. Tablet)')
        gst = to_dec(raw.get('gst_rate'), 'gst_rate', errors, required=not existing)
        if gst is not None and gst not in GST_ALLOWED:
            errors.append(f"gst_rate {gst} is not allowed; use only 0, 5, 12 or 18")
        mrp = to_dec(raw.get('mrp'), 'mrp', errors, minimum=Decimal('0'))
        reorder = to_int(raw.get('reorder_level'), 'reorder_level', errors, minimum=0)

        sched_raw = txt(raw.get('schedule')).lower()
        schedule = SCHEDULE_MAP.get(sched_raw, 'BAD')
        if schedule == 'BAD':
            errors.append(f"schedule '{txt(raw.get('schedule'))}' is invalid (NONE, H, H1 or X)")
            schedule = None

        sup_name = txt(raw.get('default_supplier'))
        sup = suppliers.get(sup_name.lower()) if sup_name else None
        if sup_name and not sup:
            errors.append(f"supplier '{sup_name}' was not found in Suppliers")

        base_name = unit or (existing.unit.name if existing and existing.unit_id else '')
        packs = []
        for i in (1, 2):
            pu = _unit_name(raw.get(f'pack{i}_unit'))
            pf = to_int(raw.get(f'pack{i}_factor'), f'pack{i}_factor', errors, minimum=2)
            if pu or pf is not None:
                if not pu or pf is None:
                    errors.append(f'fill in both pack{i}_unit and pack{i}_factor')
                elif pu.lower() == base_name.lower():
                    errors.append(f"pack{i}_unit must be different from the base unit ('{base_name}')")
                else:
                    packs.append([pu, pf])
        if len(packs) == 2 and packs[0][0].lower() == packs[1][0].lower():
            errors.append('pack1 and pack2 must use different units')

        active = to_bool(raw.get('is_active'), 'is_active', errors)

        data = dict(
            existing_id=existing.pk if existing else None, name=name, unit=unit,
            gst_rate=str(gst) if gst is not None else None, mrp=str(mrp) if mrp is not None else None,
            barcode=barcode, schedule=schedule, reorder_level=reorder, is_active=active,
            composition=txt(raw.get('composition')), hsn_code=txt(raw.get('hsn_code')),
            category=txt(raw.get('category')), brand=txt(raw.get('brand')),
            rack=txt(raw.get('rack')), row=txt(raw.get('row')), location=txt(raw.get('location')),
            default_supplier_id=sup.pk if sup else None, packs=packs,
        )
        if errors:
            action = 'error'
        elif existing:
            action = 'update' if update else 'skip'
            if action == 'skip':
                warnings.append('already exists, skipped (tick "Update records that already exist" to update it)')
        else:
            action = 'create'
        results.append(_result(n, action, data, errors, warnings, label=name))
    return results


def _apply_product(d):
    existing = Product.objects.filter(pk=d['existing_id']).first() if d['existing_id'] else None
    p = existing or Product()
    if d['name']:
        p.name = d['name']
    if d['unit']:
        p.unit = _fk(Unit, d['unit'])
    if d['gst_rate'] is not None:
        p.gst_rate = Decimal(d['gst_rate'])
    if d['mrp'] is not None:
        p.mrp = Decimal(d['mrp'])
    if d['barcode']:
        p.barcode = d['barcode']
    if d['schedule']:
        p.schedule = d['schedule']
    if d['reorder_level'] is not None and not existing:      # only a starting value; afterwards it is automatic (see levels.py)
        p.reorder_level = d['reorder_level']
    if d['is_active'] is not None:
        p.is_active = d['is_active']
    for f in ('composition', 'hsn_code', 'rack', 'row', 'location'):
        if d[f]:
            setattr(p, f, d[f])
    if d['category']:
        p.category = _fk(Category, d['category'])
    if d['brand']:
        p.brand = _fk(Brand, d['brand'])
    if d['default_supplier_id']:
        p.default_supplier_id = d['default_supplier_id']
    p.save()
    for unit_name, factor in d['packs']:
        ProductUnit.objects.update_or_create(product=p, unit=_fk(Unit, unit_name), defaults={'factor': factor})
    return p


def commit_products(results, opts):
    return _commit_each(results, _apply_product)


def export_products():
    heads = [c['name'] for c in PRODUCT_COLS] + ['stock']
    rows = []
    qs = Product.objects.select_related('unit', 'category', 'brand', 'default_supplier') \
        .prefetch_related('unit_conversions__unit').order_by('name')
    for p in qs:
        packs = sorted(p.unit_conversions.all(), key=lambda c: c.factor)
        pk = [(packs[i].unit.name, packs[i].factor) if i < len(packs) else ('', '') for i in (0, 1)]
        rows.append([
            p.name, p.unit.name if p.unit_id else '', p.gst_rate.normalize() if p.gst_rate is not None else '',
            p.composition or '', p.hsn_code or '', p.category.name if p.category_id else '',
            p.brand.name if p.brand_id else '', p.mrp if p.mrp is not None else '', p.barcode or '',
            '' if p.schedule == 'NONE' else p.schedule, p.reorder_level, p.rack or '', p.row or '', p.location or '',
            p.default_supplier.company if p.default_supplier_id else '', pk[0][0], pk[0][1], pk[1][0], pk[1][1],
            'yes' if p.is_active is not False else 'no', p.stock_qty,
        ])
    return heads, rows


# ======================================================
# CUSTOMERS
# ======================================================

CUSTOMER_COLS = [
    _col('name', True, 'Customer name', 'Ramesh Kumar', 'Sharma Medical'),
    _col('phone', False, 'Mobile number', '9876543210', '9123456789'),
    _col('address', False, 'Address', 'Salt Lake, Kolkata', ''),
    _col('gstin', False, '15-character GSTIN (for B2B)', '', '19ABCDE1234F1Z5'),
    _col('credit_limit', False, 'Credit limit (0 / blank = no limit)', '5000', '0'),
    _col('is_active', False, 'yes / no (blank = yes)', 'yes', 'yes'),
]
CUSTOMER_ALIASES = {'customer': 'name', 'customer_name': 'name', 'mobile': 'phone', 'mobile_no': 'phone',
                    'phone_number': 'phone', 'contact': 'phone', 'limit': 'credit_limit', 'gst': 'gstin',
                    'gst_no': 'gstin', 'gstin_no': 'gstin'}


def _gstin(raw, errors):
    g = txt(raw).upper().replace(' ', '')
    if g and len(g) != 15:
        errors.append(f"GSTIN '{g}' must be 15 characters")
        return ''
    return g


def validate_customers(rows, opts):
    update = bool(opts.get('update'))
    by_phone, by_name = {}, {}
    for c in Customer.objects.all():
        if _digits(c.phone):
            by_phone.setdefault(_digits(c.phone), c)
        by_name.setdefault(c.name.strip().lower(), c)
    results, seen = [], {}
    for n, raw in rows:
        errors, warnings = [], []
        name = txt(raw.get('name'))
        if not name:
            errors.append('name is required')
        phone = txt(raw.get('phone'))
        if phone and len(_digits(phone)) < 10:
            errors.append(f"phone '{phone}' must have 10 digits")
        key = _digits(phone) or name.lower()
        if key and key in seen:
            errors.append(f"this customer also appears in row {seen[key]} (duplicate in file)")
        if key:
            seen.setdefault(key, n)
        existing = (by_phone.get(_digits(phone)) if _digits(phone) else None) or by_name.get(name.lower())
        limit = to_dec(raw.get('credit_limit'), 'credit_limit', errors, minimum=Decimal('0'))
        data = dict(existing_id=existing.pk if existing else None, name=name, phone=phone,
                    address=txt(raw.get('address')), gstin=_gstin(raw.get('gstin'), errors),
                    credit_limit=str(limit) if limit is not None else None,
                    is_active=to_bool(raw.get('is_active'), 'is_active', errors))
        if errors:
            action = 'error'
        elif existing:
            action = 'update' if update else 'skip'
            if action == 'skip':
                warnings.append('already exists, skipped')
        else:
            action = 'create'
        results.append(_result(n, action, data, errors, warnings, label=name))
    return results


def _apply_customer(d):
    c = Customer.objects.filter(pk=d['existing_id']).first() if d['existing_id'] else Customer()
    c.name = d['name'] or c.name
    for f in ('phone', 'address', 'gstin'):
        if d[f]:
            setattr(c, f, d[f])
    if d['credit_limit'] is not None:
        c.credit_limit = Decimal(d['credit_limit'])
    if d['is_active'] is not None:
        c.is_active = d['is_active']
    c.save()


def commit_customers(results, opts):
    return _commit_each(results, _apply_customer)


def export_customers():
    heads = [c['name'] for c in CUSTOMER_COLS]
    rows = [[c.name, c.phone or '', c.address or '', c.gstin or '', c.credit_limit,
             'yes' if c.is_active else 'no'] for c in Customer.objects.order_by('name')]
    return heads, rows


# ======================================================
# SUPPLIERS
# ======================================================

SUPPLIER_COLS = [
    _col('company', True, 'Supplier / company name', 'ABC Pharma Distributors', 'XYZ Medico'),
    _col('person', False, 'Contact person', 'Rakesh', ''),
    _col('phone', False, 'Phone', '9876500000', ''),
    _col('email', False, 'Email', 'abc@example.com', ''),
    _col('address', False, 'Address', 'Burrabazar', ''),
    _col('city', False, 'City', 'Kolkata', 'Howrah'),
    _col('gstin', False, '15-character GSTIN (state / PAN are filled in automatically)', '19ABCDE1234F1Z5', ''),
    _col('is_active', False, 'yes / no (blank = yes)', 'yes', 'yes'),
]
SUPPLIER_ALIASES = {'supplier': 'company', 'supplier_name': 'company', 'name': 'company', 'company_name': 'company',
                    'contact_person': 'person', 'mobile': 'phone', 'gst': 'gstin', 'gst_no': 'gstin'}


def validate_suppliers(rows, opts):
    update = bool(opts.get('update'))
    by_name = {s.company.strip().lower(): s for s in Supplier.objects.all()}
    by_gstin = {s.gstin: s for s in Supplier.objects.exclude(gstin__isnull=True).exclude(gstin='')}
    results, seen, seen_g = [], {}, {}
    for n, raw in rows:
        errors, warnings = [], []
        company = txt(raw.get('company'))
        if not company:
            errors.append('company is required')
        if company and company.lower() in seen:
            errors.append(f"this supplier also appears in row {seen[company.lower()]} (duplicate in file)")
        if company:
            seen.setdefault(company.lower(), n)
        existing = by_name.get(company.lower()) if company else None
        gstin = _gstin(raw.get('gstin'), errors)
        if gstin:
            other = by_gstin.get(gstin)
            if (other and (not existing or other.pk != existing.pk)) or (gstin in seen_g and seen_g[gstin] != n):
                errors.append(f"GSTIN '{gstin}' belongs to another supplier (every supplier has a unique GSTIN)")
            seen_g.setdefault(gstin, n)
        email = txt(raw.get('email'))
        if email:
            try:
                validate_email(email)
            except ValidationError:
                errors.append(f"email '{email}' is not valid")
        data = dict(existing_id=existing.pk if existing else None, company=company, person=txt(raw.get('person')),
                    phone=txt(raw.get('phone')), email=email, address=txt(raw.get('address')),
                    city=txt(raw.get('city')), gstin=gstin,
                    is_active=to_bool(raw.get('is_active'), 'is_active', errors))
        if errors:
            action = 'error'
        elif existing:
            action = 'update' if update else 'skip'
            if action == 'skip':
                warnings.append('already exists, skipped')
        else:
            action = 'create'
        results.append(_result(n, action, data, errors, warnings, label=company))
    return results


def _apply_supplier(d):
    s = Supplier.objects.filter(pk=d['existing_id']).first() if d['existing_id'] else Supplier()
    s.company = d['company'] or s.company
    for f in ('person', 'phone', 'email', 'address', 'city', 'gstin'):
        if d[f]:
            setattr(s, f, d[f])
    if d['is_active'] is not None:
        s.is_active = d['is_active']
    s.save()


def commit_suppliers(results, opts):
    return _commit_each(results, _apply_supplier)


def export_suppliers():
    heads = [c['name'] for c in SUPPLIER_COLS]
    rows = [[s.company, s.person or '', s.phone or '', s.email or '', s.address or '', s.city or '',
             s.gstin or '', 'yes' if s.is_active is not False else 'no'] for s in Supplier.objects.order_by('company')]
    return heads, rows


# ======================================================
# STOCK-IN (Purchases)
# ======================================================

STOCK_COLS = [
    _col('supplier', True, 'Supplier name (must already exist in Suppliers)', 'ABC Pharma Distributors', 'ABC Pharma Distributors'),
    _col('invoice_number', True, 'The supplier\'s bill number. Use the same value on every row of one bill', 'INV-1001', 'INV-1001'),
    _col('product', True, 'Medicine name or barcode. A name that is not in Products yet is created automatically (see the optional "new product" columns below)', 'Dolo 650', 'Azithral 500'),
    _col('qty', True, 'Quantity (in the unit given below)', '20', '10'),
    _col('purchase_price', True, 'TOTAL price of this row, including GST (not the per-piece rate)', '2100', '1850'),
    _col('exp_date', True, 'Expiry: 31/08/2027 or 08/27', '08/27', '12/26'),
    _col('selling_price', False, 'Selling price = MRP incl. GST of 1 unit given below (e.g. per Strip). Updates the product MRP, so billing fills the rate', '31.50', '95'),
    _col('invoice_date', False, 'Bill date (blank = today). Keep it the same within one bill', '05/10/2026', '05/10/2026'),
    _col('unit', False, 'Unit of the qty, e.g. Strip (blank = the product\'s base unit)', 'Strip', 'Strip'),
    _col('batch', False, 'Batch number (blank = generated automatically)', 'B2301', 'AZ778'),
    _col('mfg_date', False, 'Manufacturing date', '09/25', '06/25'),
    _col('rack', False, 'Rack', 'A1', 'A2'),
    _col('row', False, 'Row', '2', '1'),
    _col('location', False, 'Godown / location', '', ''),
    _col('gst_rate', False, 'NEW product only: GST % (0, 5, 12 or 18). Required when the medicine is not in Products yet', '12', '12', need='Needed for a new medicine'),
    _col('base_unit', False, 'NEW product only: smallest unit, e.g. Tablet. Blank = the "unit" column is used (when there is no pack_unit / pack_size)', 'Tablet', 'Tablet', need='Needed for a new medicine (or fill unit)'),
    _col('pack_unit', False, 'NEW product only: larger pack, e.g. Strip (fill together with pack_size)', 'Strip', 'Strip', need='With pack_size'),
    _col('pack_size', False, 'NEW product only: base units in 1 pack (e.g. 15 tablets in a Strip)', '15', '10', need='With pack_unit'),
    _col('composition', False, 'NEW product only: salt / composition', 'Paracetamol 650mg', 'Azithromycin 500mg'),
    _col('category', False, 'NEW product only: category (created if it is new)', 'Tablet', 'Tablet'),
    _col('brand', False, 'NEW product only: brand / company (created if it is new)', 'Micro Labs', 'Alembic'),
    _col('hsn_code', False, 'NEW product only: HSN code', '3004', '3004'),
    _col('barcode', False, 'Barcode (a new product gets it; for an existing product it is used to find the product)', '8901234567890', ''),
    _col('schedule', False, 'NEW product only: drug schedule: blank/NONE, H, H1 or X', '', 'H'),
]
STOCK_ALIASES = {'supplier_name': 'supplier', 'party': 'supplier', 'invoice': 'invoice_number',
                 'invoice_no': 'invoice_number', 'bill_no': 'invoice_number', 'bill_number': 'invoice_number',
                 'date': 'invoice_date', 'bill_date': 'invoice_date', 'product_name': 'product',
                 'medicine': 'product', 'item': 'product', 'quantity': 'qty',
                 'price': 'purchase_price', 'total_price': 'purchase_price', 'amount': 'purchase_price',
                 'expiry': 'exp_date', 'expiry_date': 'exp_date', 'exp': 'exp_date', 'mfg': 'mfg_date',
                 'batch_no': 'batch', 'batch_number': 'batch', 'mrp': 'selling_price', 'selling': 'selling_price',
                 'sale_price': 'selling_price', 'selling_rate': 'selling_price',
                 'gst': 'gst_rate', 'hsn': 'hsn_code', 'salt': 'composition', 'pack': 'pack_unit',
                 'strip_size': 'pack_size', 'pack_factor': 'pack_size', 'smallest_unit': 'base_unit'}


BARCODE_LIKE = re.compile(r'\d{8,}')


def _new_product_spec(raw, pname, known, barcodes, errors):
    """Details of a medicine that is not in Products yet, taken from the stock row (or from an earlier row of the same
    name). Returns a JSON-safe dict, or None after adding to `errors`."""
    if BARCODE_LIKE.fullmatch(pname):
        errors.append(f"'{pname}' looks like a barcode that is not in Products. Enter the medicine name so the product can be created")
        return None
    if len(pname) > 255:
        errors.append('product name is too long')
        return None
    if pname.lower() in known:
        return known[pname.lower()]
    start = len(errors)
    barcode = txt(raw.get('barcode')) or None
    if barcode and len(barcode) > 64:
        errors.append('barcode is longer than 64 characters')
    elif barcode and barcodes.get(barcode, pname.lower()) != pname.lower():
        errors.append(f"barcode '{barcode}' is also used for the new product '{barcodes[barcode]}'")
    sched_raw = txt(raw.get('schedule')).lower()
    schedule = SCHEDULE_MAP.get(sched_raw, 'BAD')
    if schedule == 'BAD':
        errors.append(f"schedule '{txt(raw.get('schedule'))}' is invalid (NONE, H, H1 or X)")
        schedule = None
    gst = to_dec(raw.get('gst_rate'), 'gst_rate', errors)
    base = _unit_name(raw.get('base_unit'))
    pack_unit = _unit_name(raw.get('pack_unit'))
    pack_size = to_int(raw.get('pack_size'), 'pack_size', errors, minimum=2)
    if gst is None:
        errors.append(f"product '{pname}' is not in Products. To create it, fill in gst_rate (0, 5, 12 or 18) and base_unit, or add it in Products first")
    elif gst not in GST_ALLOWED:
        errors.append(f"gst_rate {gst} is not allowed; use only 0, 5, 12 or 18")
    if not base and not pack_unit and pack_size is None:
        base = _unit_name(raw.get('unit'))              # no packs: the unit of the qty is the unit the stock is counted in
    if not base:
        errors.append(f"product '{pname}' is not in Products. To create it, fill in base_unit (the smallest unit, e.g. Tablet)"
                      + (" - it is needed because you filled pack_unit / pack_size" if pack_unit or pack_size is not None else ' or unit'))
    if pack_unit or pack_size is not None:
        if not pack_unit or pack_size is None:
            errors.append('fill in both pack_unit and pack_size')
        elif pack_unit.lower() == base.lower():
            errors.append(f"pack_unit must be different from the base unit ('{base}')")
    if len(errors) > start:
        return None
    spec = dict(name=pname, gst_rate=str(gst), base_unit=base,
                pack_unit=pack_unit if pack_size is not None else '', pack_size=pack_size or 0,
                composition=txt(raw.get('composition')), hsn_code=txt(raw.get('hsn_code')),
                category=txt(raw.get('category')), brand=txt(raw.get('brand')), barcode=barcode, schedule=schedule)
    known[pname.lower()] = spec
    if barcode:
        barcodes[barcode] = pname.lower()
    return spec


def validate_stock(rows, opts):
    create_new = bool(opts.get('create_products'))
    new_specs, new_codes = {}, {}
    products_by_name, products_by_code = {}, {}
    for p in Product.objects.select_related('unit'):
        products_by_name.setdefault((p.name or '').strip().lower(), p)
        if p.barcode:
            products_by_code[p.barcode] = p
    suppliers = {s.company.strip().lower(): s for s in Supplier.objects.all()}
    units = {u.name.strip().lower(): u for u in Unit.objects.all()}
    existing_invoices = {(sid, (inv or '').strip().lower())
                         for sid, inv in Purchase.objects.values_list('supplier_id', 'invoice_number')}
    today = timezone.localdate()

    results, groups, group_date = [], {}, {}
    for n, raw in rows:
        errors, warnings = [], []
        sup_name = txt(raw.get('supplier'))
        sup = suppliers.get(sup_name.lower()) if sup_name else None
        if not sup_name:
            errors.append('supplier is required')
        elif not sup:
            errors.append(f"supplier '{sup_name}' was not found (import/add Suppliers first)")
        invoice = txt(raw.get('invoice_number'))
        if not invoice:
            errors.append('invoice_number is required')

        pname = txt(raw.get('product'))
        bcode = txt(raw.get('barcode'))
        by_name = (products_by_code.get(pname) or products_by_name.get(pname.lower())) if pname else None
        by_code = products_by_code.get(bcode) if bcode else None
        if by_name and by_code and by_name.pk != by_code.pk:
            errors.append(f"barcode '{bcode}' belongs to another product ('{by_code.name}')")
        elif by_code and not by_name and pname and (by_code.name or '').strip().lower() != pname.lower():
            errors.append(f"barcode '{bcode}' belongs to '{by_code.name}' in the system, but the name in the file is different. Please check the name/barcode")
        prod = by_name or by_code
        if not pname and not prod:
            pname = bcode
            if not pname:
                errors.append('product is required')
        elif not pname and prod:
            pname = prod.name or ''
        spec = None
        if pname and not prod:
            if create_new:
                spec = _new_product_spec(raw, pname, new_specs, new_codes, errors)
                if spec:
                    warnings.append(f"new product '{spec['name']}' will be created (GST {spec['gst_rate']}%, unit {spec['base_unit']})")
            else:
                errors.append(f"product '{pname}' was not found (add it in Products, or tick \"Create new products\" and fill gst_rate and base_unit)")

        qty = to_int(raw.get('qty'), 'qty', errors, required=True, minimum=1)
        price = to_dec(raw.get('purchase_price'), 'purchase_price', errors, required=True, minimum=Decimal('0'))
        selling = to_dec(raw.get('selling_price'), 'selling_price', errors, minimum=Decimal('0'))
        if selling is not None and selling <= 0:
            errors.append('selling_price must be greater than 0 (leave it blank to keep the current MRP)')
        exp = to_date(raw.get('exp_date'), 'exp_date', errors, required=True, month_end=True)
        mfg = to_date(raw.get('mfg_date'), 'mfg_date', errors)
        inv_date = to_date(raw.get('invoice_date'), 'invoice_date', errors) or today
        if mfg and exp and exp < mfg:
            errors.append('exp_date cannot be before mfg_date')
        if exp and exp < today:
            warnings.append('this batch has already expired')
        if inv_date > today:
            errors.append('invoice_date cannot be in the future')

        unit_name = _unit_name(raw.get('unit'))
        unit = None
        row_unit = ''                 # name of the qty unit when it is a pack of a product that is about to be created
        if spec:
            if unit_name and unit_name.lower() == spec['pack_unit'].lower() and spec['pack_unit']:
                row_unit = spec['pack_unit']
            elif unit_name and unit_name.lower() != spec['base_unit'].lower():
                errors.append(f"unit '{unit_name}' must be the base unit '{spec['base_unit']}'" + (f" or the pack unit '{spec['pack_unit']}'" if spec['pack_unit'] else ''))
        elif unit_name:
            unit = units.get(unit_name.lower())
            if not unit:
                errors.append(f"unit '{unit_name}' does not exist in the system")
        if prod and not errors:
            try:
                prod.unit_factor(unit)
            except ValidationError as e:
                errors.append(' '.join(e.messages))

        gkey = ((sup.pk if sup else sup_name.lower()), invoice.lower())
        if sup and invoice:
            if (sup.pk, invoice.lower()) in existing_invoices:
                errors.append(f"invoice '{invoice}' for this supplier has already been entered (to avoid double entry)")
            first = group_date.setdefault(gkey, (inv_date, n))
            if first[0] != inv_date and txt(raw.get('invoice_date')):
                errors.append(f"the date of this invoice differs from row {first[1]}; one invoice can have only one date")
        groups.setdefault(gkey, []).append(len(results))

        data = dict(
            supplier_id=sup.pk if sup else None, supplier=sup.company if sup else sup_name, invoice=invoice,
            invoice_date=inv_date.isoformat(), product_id=prod.pk if prod else None,
            product=prod.name if prod else pname, qty=qty, unit_id=unit.pk if unit else None,
            unit=unit.name if unit else (row_unit or (spec['base_unit'] if spec else (prod.unit.name if prod and prod.unit_id else ''))),
            new_product=spec, unit_name=row_unit,
            price=str(price) if price is not None else None,
            selling=str(selling) if selling is not None and selling > 0 else None, exp=exp.isoformat() if exp else None,
            mfg=mfg.isoformat() if mfg else None, batch=txt(raw.get('batch')),
            rack=txt(raw.get('rack')), row=txt(raw.get('row')), location=txt(raw.get('location')),
            group=f"{gkey[0]}|{gkey[1]}",
        )
        results.append(_result(n, 'error' if errors else 'create', data, errors, warnings,
                               label=f"{invoice} · {data['product']}"))

    # If any row of an invoice is invalid, the whole invoice is skipped (so a half bill is never added to stock)
    for idxs in groups.values():
        if any(results[i]['action'] == 'error' for i in idxs):
            first_bad = next(results[i]['n'] for i in idxs if results[i]['action'] == 'error')
            for i in idxs:
                if results[i]['action'] != 'error':
                    results[i]['action'] = 'error'
                    results[i]['errors'].append(f"row {first_bad} of this invoice has an error, so the whole invoice is skipped")
    return results


def _stock_product_id(d):
    """Product of a stock row. A new medicine is created here (inside the invoice's transaction, so a failed invoice
    leaves no product behind); the name is looked up every time so two rows of one medicine share one product."""
    if d.get('product_id'):
        return d['product_id']
    spec = d['new_product']
    p = Product.objects.filter(name__iexact=spec['name']).first()
    if not p:
        p = _apply_product(dict(
            existing_id=None, name=spec['name'], unit=spec['base_unit'], gst_rate=spec['gst_rate'], mrp=None,
            barcode=spec.get('barcode'), schedule=spec.get('schedule'), reorder_level=None, is_active=None, composition=spec['composition'],
            hsn_code=spec['hsn_code'], rack=d['rack'], row=d['row'], location=d['location'],
            category=spec['category'], brand=spec['brand'], default_supplier_id=None,
            packs=[[spec['pack_unit'], spec['pack_size']]] if spec['pack_unit'] and spec['pack_size'] else []))
    return p.pk


def commit_stock(results, opts):
    summary = dict(created=0, updated=0, skipped=0, failed=[], notes=[])
    groups = {}
    for r in results:
        if r['action'] == 'create':
            groups.setdefault(r['data']['group'], []).append(r)
        else:
            summary['skipped'] += 1
    invoices = 0
    for rs in groups.values():
        d0 = rs[0]['data']
        try:
            with transaction.atomic():
                purchase = Purchase.objects.create(
                    supplier_id=d0['supplier_id'], invoice_number=d0['invoice'],
                    invoice_date=date.fromisoformat(d0['invoice_date']))
                for r in rs:
                    d = r['data']
                    product_id = _stock_product_id(d)
                    unit_id = d['unit_id'] or (_fk(Unit, d['unit_name']).pk if d.get('unit_name') else None)
                    item = PurchaseItem(
                        purchase=purchase, product_id=product_id, qty=d['qty'], unit_id=unit_id,
                        purchase_price=Decimal(d['price']),
                        selling_price=Decimal(d['selling']) if d.get('selling') else None,
                        expire_date=date.fromisoformat(d['exp']),
                        manufacture_date=date.fromisoformat(d['mfg']) if d['mfg'] else None,
                        batch=d['batch'] or None, rack=d['rack'] or None, row=d['row'] or None,
                        location=d['location'] or None)
                    item.clean()
                    item.save()
            summary['created'] += len(rs)
            invoices += 1
        except (ValidationError, IntegrityError, ValueError) as e:
            msg = ' '.join(e.messages) if isinstance(e, ValidationError) else str(e)
            for r in rs:
                summary['failed'].append((r['n'], f"invoice '{d0['invoice']}': {msg}"))
    summary['notes'].append(f"{invoices} invoice(s) / purchase(s) created.")
    return summary


# ======================================================
# EXPENSES
# ======================================================

EXPENSE_COLS = [
    _col('name', True, 'Expense type: Rent, Salary, Electricity, Water, Maintenance or Other', 'Rent', 'Electricity'),
    _col('amount', True, 'Amount in rupees', '15000', '2350.50'),
    _col('date', False, 'Date (blank = today)', '01/10/2026', '05/10/2026'),
]
EXPENSE_ALIASES = {'type': 'name', 'expense': 'name', 'expense_type': 'name', 'head': 'name', 'category': 'name',
                   'amt': 'amount', 'rs': 'amount', 'rupees': 'amount'}


def validate_expenses(rows, opts):
    types = {code.lower(): code for code, _ in Expense.EXPENSES_TYPES}
    types.update({label.lower(): code for code, label in Expense.EXPENSES_TYPES})
    have = {(d, n, a) for d, n, a in Expense.objects.values_list('date', 'name', 'amount')}
    today = timezone.localdate()
    results = []
    for n, raw in rows:
        errors, warnings = [], []
        type_raw = txt(raw.get('name'))
        code = types.get(type_raw.lower())
        if not type_raw:
            errors.append('name (expense type) is required')
        elif not code:
            errors.append(f"type '{type_raw}' is invalid (Rent, Salary, Electricity, Water, Maintenance or Other)")
        amount = to_dec(raw.get('amount'), 'amount', errors, required=True)
        if amount is not None and amount <= 0:
            errors.append('amount must be greater than 0')
            amount = None
        when = to_date(raw.get('date'), 'date', errors) or today
        if when > today:
            errors.append('date cannot be in the future')
        data = dict(name=code, amount=str(amount) if amount is not None else None, date=when.isoformat())
        if errors:
            action = 'error'
        elif (when, code, amount) in have:
            action = 'skip'
            warnings.append('an expense with the same date, type and amount already exists, skipped')
        else:
            action = 'create'
        results.append(_result(n, action, data, errors, warnings, label=f"{type_raw} · ₹{amount}"))
    return results


def _apply_expense(d):
    Expense.objects.create(name=d['name'], amount=Decimal(d['amount']), date=date.fromisoformat(d['date']))


def commit_expenses(results, opts):
    return _commit_each(results, _apply_expense)


def export_expenses():
    return ['name', 'amount', 'date'], [[e.get_name_display(), e.amount, e.date] for e in Expense.objects.order_by('-date', '-id')]


# ======================================================
# CATEGORIES / BRANDS / UNITS  (name only)
# ======================================================

def _simple_importer(model, samples, alias_units=False):
    existing_label = {'Unit': 'unit', 'Brand': 'brand', 'Category': 'category'}[model.__name__]

    def validate(rows, opts):
        have = {(o.name or '').strip().lower() for o in model.objects.all()}
        results, seen = [], {}
        for n, raw in rows:
            errors, warnings = [], []
            name = _unit_name(raw.get('name')) if alias_units else txt(raw.get('name'))
            if not name:
                errors.append('name is required')
            elif len(name) > 255:
                errors.append('name must be 255 characters or fewer')
            elif name.lower() in seen:
                errors.append(f"this name also appears in row {seen[name.lower()]} (duplicate in file)")
            if name:
                seen.setdefault(name.lower(), n)
            if errors:
                action = 'error'
            elif name.lower() in have:
                action = 'skip'
                warnings.append(f'this {existing_label} already exists, skipped')
            else:
                action = 'create'
            results.append(_result(n, action, dict(name=name), errors, warnings, label=name))
        return results

    def apply(d):
        if not model.objects.filter(name__iexact=d['name']).exists():
            model.objects.create(name=d['name'])

    def commit(results, opts):
        return _commit_each(results, apply)

    def export():
        return ['name'], [[o.name] for o in model.objects.order_by('name')]

    return dict(cols=[_col('name', True, f'{existing_label.capitalize()} name', *samples)],
                aliases={existing_label: 'name', f'{existing_label}_name': 'name'},
                validate=validate, commit=commit, export=export, has_update=False, max_rows=3000)


# ======================================================
# Common commit + registry
# ======================================================

def _commit_each(results, apply):
    summary = dict(created=0, updated=0, skipped=0, failed=[], notes=[])
    for r in results:
        if r['action'] in ('create', 'update'):
            try:
                with transaction.atomic():
                    apply(r['data'])
                summary['created' if r['action'] == 'create' else 'updated'] += 1
            except (ValidationError, IntegrityError, ValueError) as e:
                msg = ' '.join(e.messages) if isinstance(e, ValidationError) else str(e)
                summary['failed'].append((r['n'], msg))
        else:
            summary['skipped'] += 1
    return summary


IMPORTERS = {
    'products': dict(
        title='Products', icon='🧴', cols=PRODUCT_COLS, aliases=PRODUCT_ALIASES, validate=validate_products,
        commit=commit_products, export=export_products, has_update=True, max_rows=3000,
        desc='Add a whole list of medicines at once. Name, unit and GST are required. You can also fill in barcode, MRP and packs (Strip/Box).',
        after='Next, add stock using Stock Entry.'),
    'customers': dict(
        title='Customers', icon='👥', cols=CUSTOMER_COLS, aliases=CUSTOMER_ALIASES, validate=validate_customers,
        commit=commit_customers, export=export_customers, has_update=True, max_rows=3000,
        desc='Regular customers and their credit limits. Existing credit balances are not entered here; they build up from bills and receipts.',
        after=''),
    'suppliers': dict(
        title='Suppliers', icon='🏭', cols=SUPPLIER_COLS, aliases=SUPPLIER_ALIASES, validate=validate_suppliers,
        commit=commit_suppliers, export=export_suppliers, has_update=True, max_rows=3000,
        desc='List of distributors / suppliers. When a GSTIN is given, the state and PAN are filled in automatically.',
        after='Suppliers must exist before a Stock Entry (purchase) import. Products do not: new medicines are created from the purchase rows.'),
    'stock': dict(
        title='Stock Entry (Purchases)', icon='📦', cols=STOCK_COLS, aliases=STOCK_ALIASES, validate=validate_stock,
        commit=commit_stock, export=None, has_update=False, max_rows=1500, can_create_products=True, barcode_as_product=True,
        desc='Create purchases and stock/batches at once from supplier bill rows. Use the same supplier and invoice number on every row of one invoice. '
             'Use this for opening stock as well.',
        after='The created bills will appear on the Purchases page. If there is a mistake, create a Purchase Return from there.'),
}
IMPORTERS['expenses'] = dict(
    title='Expenses', icon='💸', cols=EXPENSE_COLS, aliases=EXPENSE_ALIASES, validate=validate_expenses,
    commit=commit_expenses, export=export_expenses, has_update=False, max_rows=3000,
    desc='Add expenses (Rent, Salary, Electricity...) at once. A row with the same date, type and amount is not imported again.',
    after='These expenses are included in Profit & Loss automatically.')
IMPORTERS['categories'] = dict(
    title='Categories', icon='🗂️', desc='Medicine categories (Tablet, Syrup, Injection...). A category written in the Products import is also created automatically.',
    after='', **_simple_importer(Category, ('Tablet', 'Syrup')))
IMPORTERS['brands'] = dict(
    title='Brands', icon='🏷️', desc='Brand / company names (Cipla, Sun Pharma...). A brand written in the Products import is also created automatically.',
    after='', **_simple_importer(Brand, ('Cipla', 'Sun Pharma')))
IMPORTERS['units'] = dict(
    title='Units', icon='📏', desc='Units (Tablet, Strip, Box, Bottle...). Short names (tab, strips, btl) are converted to the correct name automatically.',
    after='Conversions for larger packs such as Strip/Box are set from the pack1/pack2 columns of the Products import.',
    **_simple_importer(Unit, ('Tablet', 'Strip'), alias_units=True))
IMPORTERS['stock']['desc_extra'] = ('One row = one medicine. For a bill with several medicines, add one row per medicine and repeat the same supplier and invoice_number on each row. '
                                    'You can also put several invoices (and suppliers) in one file. If even one row of an invoice is invalid, the whole invoice is skipped.')


def _column_order(c):
    """Required columns first, then the ones needed only in some cases (e.g. a new medicine), then optional ones."""
    return 0 if c['required'] else (1 if c.get('need') else 2)


for _imp in IMPORTERS.values():
    if _imp.get('cols'):
        _imp['cols'] = sorted(_imp['cols'], key=_column_order)      # sorted() is stable: the original order is kept inside each group


def display_header(name):
    """Header shown in downloaded templates: first letter capital (invoice_number -> Invoice_number). Matching ignores case."""
    return name[:1].upper() + name[1:]


def layout_signature(headers):
    """Identifies a file layout (e.g. one supplier's export) by the set of its column headers."""
    return hashlib.sha1('|'.join(sorted(h for h in headers if h)).encode('utf-8')).hexdigest()


def canonical_columns(imp, headers, extra=None):
    """Map the file's headers to the importer's standard column names (for mapped_rows).
    `extra` = the user's own choices {header: column or ''}: they win over the automatic names ('' = ignore the column)."""
    names = {c['name'] for c in imp['cols']}
    extra = {h: k for h, k in (extra or {}).items() if k == '' or k in names}
    out = {}
    for h in headers:
        if h in extra:
            key = extra[h]
        else:
            key = h if h in names else imp['aliases'].get(h)
        if key and key not in out.values():
            out[h] = key
    if imp.get('barcode_as_product') and 'product' not in out.values():      # a file with only a barcode column: it identifies the product
        for h, k in out.items():
            if k == 'barcode':
                out[h] = 'product'
    return out


def prepare_rows(imp, headers, rows, extra=None):
    """Renames headers using the aliases and converts rows into dicts with standard names."""
    mapping = canonical_columns(imp, headers, extra)
    missing = [c['name'] for c in imp['cols'] if c['required'] and c['name'] not in mapping.values()]
    ignored = [h for h in headers if h and h not in mapping]
    mapped = [(n, {mapping[h]: v for h, v in raw.items() if h in mapping}) for n, raw in rows]
    return mapped, missing, ignored


# ======================================================
# Temp store (between preview and confirm)
# ======================================================

STORE = Path(tempfile.gettempdir()) / 'medsync_imports'
_TOKEN = re.compile(r'^[0-9a-f]{32}$')


def _json_default(o):
    if isinstance(o, datetime):
        return o.date().isoformat() if not (o.hour or o.minute or o.second) else o.isoformat()
    if isinstance(o, date):
        return o.isoformat()
    return str(o)


def stash(payload):
    STORE.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for f in STORE.glob('*.json'):          # clean up old files
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    token = secrets.token_hex(16)
    (STORE / f'{token}.json').write_text(json.dumps(payload, default=_json_default), encoding='utf-8')
    return token


def load_stash(token):
    if not token or not _TOKEN.match(token):
        return None
    f = STORE / f'{token}.json'
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def drop_stash(token):
    if token and _TOKEN.match(token):
        try:
            os.remove(STORE / f'{token}.json')
        except OSError:
            pass


# ======================================================
# Download helpers (import page + Export on every list page)
# ======================================================

_NUM = re.compile(r'^-?\u20b9?\s?(0|[1-9]\d{0,8})(\.\d+)?$')


def export_value(v):
    """Turns list-page text ('₹1,250.00', '12') into a real number in Excel, so it can be summed/sorted."""
    s = plain(v).replace(',', '')
    if _NUM.match(s):
        return float(s.replace('\u20b9', '').strip())
    return plain(v)


def table_download(filename, headers, rows, fmt='xlsx', required=()):
    fmt = 'csv' if fmt == 'csv' else 'xlsx'
    if fmt == 'csv':
        resp = HttpResponse(build_csv(headers, rows), content_type='text/csv; charset=utf-8')
    else:
        data = build_xlsx([dict(title='Data', headers=headers, rows=rows, required=set(required))])
        resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}.{fmt}"'
    return resp


def template_download(kind):
    imp = IMPORTERS[kind]
    heads = [display_header(c['name']) for c in imp['cols']]
    required = {display_header(c['name']) for c in imp['cols'] if c['required']}
    data = build_xlsx([
        dict(title='Data', headers=heads, rows=[], required=required),
        dict(title='Example', headers=heads, rows=[list(c['samples'][i] for c in imp['cols']) for i in (0, 1)],
             required=required),
        dict(title='Help', headers=['Column', 'Required?', 'What to enter'],
             rows=[[display_header(c['name']), 'YES' if c['required'] else (c.get('need') or 'optional'), c['help']] for c in imp['cols']]),
    ])
    resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="medsync_{kind}_template.xlsx"'
    return resp
