"""
Bulk import: Products, Customers, Suppliers, Stock-in (Purchases).

Flow (har importer ke liye same):
  1. validate()  -> file ki har row check hoti hai, DB me kuch nahi likhta (preview ke liye)
  2. stash()     -> saaf ki hui rows temp file me
  3. commit()    -> sirf sahi rows save hoti hain (har row/invoice apne transaction me, model ke save() se,
                    isliye stock / batch / supplier balance / ledger sab apne aap sahi bante hain)
"""
import json
import os
import re
import secrets
import tempfile
import time
from datetime import date
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

# Excel me aksar short naam likhe hote hain. Ye unhe sahi Unit se jod deta hai.
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


def _col(name, required=False, help='', s1='', s2=''):
    return dict(name=name, required=required, help=help, samples=(s1, s2))


def _unit_name(raw):
    s = txt(raw)
    return UNIT_ALIASES.get(s.lower(), s) if s else ''


def _digits(phone):
    return re.sub(r'\D', '', phone or '')[-10:]


def _result(n, action, data=None, errors=None, warnings=None, label=''):
    return dict(n=n, action=action, data=data or {}, errors=errors or [], warnings=warnings or [], label=label)


def _fk(model, name):
    """Naam se Category/Brand/Unit lo, na mile to bana do (iexact)."""
    name = (name or '').strip()
    if not name:
        return None
    return model.objects.filter(name__iexact=name).first() or model.objects.create(name=name)


# ======================================================
# PRODUCTS
# ======================================================

PRODUCT_COLS = [
    _col('name', True, 'Dawa ka naam', 'Dolo 650', 'Azithral 500'),
    _col('unit', True, 'Sabse chhoti unit (stock isi me count hota hai): Tablet, Capsule, Bottle, ML...', 'Tablet', 'Tablet'),
    _col('gst_rate', True, 'GST %: sirf 0, 5, 12 ya 18', '12', '12'),
    _col('composition', False, 'Salt / composition', 'Paracetamol 650mg', 'Azithromycin 500mg'),
    _col('hsn_code', False, 'HSN code', '3004', '3004'),
    _col('category', False, 'Category (nayi ho to bana di jayegi)', 'Tablet', 'Tablet'),
    _col('brand', False, 'Brand / company (nayi ho to bana di jayegi)', 'Micro Labs', 'Alembic'),
    _col('mrp', False, 'MRP / sale rate per base unit (billing me yahi rate bharta hai)', '2.10', '19.50'),
    _col('barcode', False, 'Barcode (har product ka alag)', '8901234567890', ''),
    _col('schedule', False, 'Drug schedule: khali/NONE, H, H1 ya X', '', 'H'),
    _col('reorder_level', False, 'Itna ya kam stock bache to low-stock alert (base unit me). Khali = 5', '100', '50'),
    _col('rack', False, 'Rack', 'A1', 'A2'),
    _col('row', False, 'Row', '2', '1'),
    _col('location', False, 'Godown / location', 'Counter', 'Counter'),
    _col('default_supplier', False, 'Supplier ka naam (pehle se Suppliers me hona chahiye)', '', ''),
    _col('pack1_unit', False, 'Bada pack, jaise Strip', 'Strip', 'Strip'),
    _col('pack1_factor', False, '1 pack1 me kitni base unit (jaise 15)', '15', '5'),
    _col('pack2_unit', False, 'Doosra bada pack, jaise Box', 'Box', ''),
    _col('pack2_factor', False, '1 pack2 me kitni base unit (jaise 150)', '150', ''),
    _col('is_active', False, 'yes / no (khali = yes)', 'yes', 'yes'),
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
            errors.append('name zaroori hai')
        barcode = txt(raw.get('barcode')) or None
        if barcode and len(barcode) > 64:
            errors.append('barcode 64 characters se bada hai')

        pb = by_barcode.get(barcode) if barcode else None
        pn = by_name.get(name.lower()) if name else None
        if pb and pn and pb.pk != pn.pk:
            errors.append(f"barcode '{barcode}' kisi aur product ('{pb.name}') ka hai")
        if pb and not pn and name and (pb.name or '').strip().lower() != name.lower():
            errors.append(f"barcode '{barcode}' system me '{pb.name}' ka hai, file me naam alag hai. Naam/barcode check karein")
        existing = pb or pn

        if name and name.lower() in seen_name:
            errors.append(f"is naam ki row {seen_name[name.lower()]} pehle aa chuki hai (file me duplicate)")
        if barcode and barcode in seen_code:
            errors.append(f"barcode '{barcode}' row {seen_code[barcode]} me bhi hai")
        if name:
            seen_name.setdefault(name.lower(), n)
        if barcode:
            seen_code.setdefault(barcode, n)

        unit = _unit_name(raw.get('unit'))
        if not unit and not existing:
            errors.append('unit zaroori hai (jaise Tablet)')
        gst = to_dec(raw.get('gst_rate'), 'gst_rate', errors, required=not existing)
        if gst is not None and gst not in GST_ALLOWED:
            errors.append(f"gst_rate {gst} nahi chalega, sirf 0, 5, 12 ya 18")
        mrp = to_dec(raw.get('mrp'), 'mrp', errors, minimum=Decimal('0'))
        reorder = to_int(raw.get('reorder_level'), 'reorder_level', errors, minimum=0)

        sched_raw = txt(raw.get('schedule')).lower()
        schedule = SCHEDULE_MAP.get(sched_raw, 'BAD')
        if schedule == 'BAD':
            errors.append(f"schedule '{txt(raw.get('schedule'))}' galat hai (NONE, H, H1 ya X)")
            schedule = None

        sup_name = txt(raw.get('default_supplier'))
        sup = suppliers.get(sup_name.lower()) if sup_name else None
        if sup_name and not sup:
            errors.append(f"supplier '{sup_name}' Suppliers me nahi mila")

        base_name = unit or (existing.unit.name if existing and existing.unit_id else '')
        packs = []
        for i in (1, 2):
            pu = _unit_name(raw.get(f'pack{i}_unit'))
            pf = to_int(raw.get(f'pack{i}_factor'), f'pack{i}_factor', errors, minimum=2)
            if pu or pf is not None:
                if not pu or pf is None:
                    errors.append(f'pack{i}_unit aur pack{i}_factor dono bharein')
                elif pu.lower() == base_name.lower():
                    errors.append(f"pack{i}_unit base unit ('{base_name}') se alag hona chahiye")
                else:
                    packs.append([pu, pf])
        if len(packs) == 2 and packs[0][0].lower() == packs[1][0].lower():
            errors.append('pack1 aur pack2 ki unit alag-alag honi chahiye')

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
                warnings.append('pehle se hai, chhod diya (update ke liye "Jo pehle se hain unhe update karo" tick karein)')
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
    if d['reorder_level'] is not None:
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
    _col('name', True, 'Customer ka naam', 'Ramesh Kumar', 'Sharma Medical'),
    _col('phone', False, 'Mobile number', '9876543210', '9123456789'),
    _col('address', False, 'Pata', 'Salt Lake, Kolkata', ''),
    _col('gstin', False, '15 character GSTIN (B2B ke liye)', '', '19ABCDE1234F1Z5'),
    _col('credit_limit', False, 'Udhar ki limit (0 / khali = koi limit nahi)', '5000', '0'),
    _col('is_active', False, 'yes / no (khali = yes)', 'yes', 'yes'),
]
CUSTOMER_ALIASES = {'customer': 'name', 'customer_name': 'name', 'mobile': 'phone', 'mobile_no': 'phone',
                    'phone_number': 'phone', 'contact': 'phone', 'limit': 'credit_limit', 'gst': 'gstin',
                    'gst_no': 'gstin', 'gstin_no': 'gstin'}


def _gstin(raw, errors):
    g = txt(raw).upper().replace(' ', '')
    if g and len(g) != 15:
        errors.append(f"GSTIN '{g}' 15 characters ka hona chahiye")
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
            errors.append('name zaroori hai')
        phone = txt(raw.get('phone'))
        if phone and len(_digits(phone)) < 10:
            errors.append(f"phone '{phone}' me 10 digit hone chahiye")
        key = _digits(phone) or name.lower()
        if key and key in seen:
            errors.append(f"ye customer row {seen[key]} me bhi hai (file me duplicate)")
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
                warnings.append('pehle se hai, chhod diya')
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
    _col('company', True, 'Supplier / company ka naam', 'ABC Pharma Distributors', 'XYZ Medico'),
    _col('person', False, 'Contact person', 'Rakesh', ''),
    _col('phone', False, 'Phone', '9876500000', ''),
    _col('email', False, 'Email', 'abc@example.com', ''),
    _col('address', False, 'Pata', 'Burrabazar', ''),
    _col('city', False, 'Sheher', 'Kolkata', 'Howrah'),
    _col('gstin', False, '15 character GSTIN (state / PAN apne aap nikal jayega)', '19ABCDE1234F1Z5', ''),
    _col('is_active', False, 'yes / no (khali = yes)', 'yes', 'yes'),
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
            errors.append('company zaroori hai')
        if company and company.lower() in seen:
            errors.append(f"ye supplier row {seen[company.lower()]} me bhi hai (file me duplicate)")
        if company:
            seen.setdefault(company.lower(), n)
        existing = by_name.get(company.lower()) if company else None
        gstin = _gstin(raw.get('gstin'), errors)
        if gstin:
            other = by_gstin.get(gstin)
            if (other and (not existing or other.pk != existing.pk)) or (gstin in seen_g and seen_g[gstin] != n):
                errors.append(f"GSTIN '{gstin}' kisi aur supplier ka hai (har supplier ka GSTIN alag hota hai)")
            seen_g.setdefault(gstin, n)
        email = txt(raw.get('email'))
        if email:
            try:
                validate_email(email)
            except ValidationError:
                errors.append(f"email '{email}' sahi nahi hai")
        data = dict(existing_id=existing.pk if existing else None, company=company, person=txt(raw.get('person')),
                    phone=txt(raw.get('phone')), email=email, address=txt(raw.get('address')),
                    city=txt(raw.get('city')), gstin=gstin,
                    is_active=to_bool(raw.get('is_active'), 'is_active', errors))
        if errors:
            action = 'error'
        elif existing:
            action = 'update' if update else 'skip'
            if action == 'skip':
                warnings.append('pehle se hai, chhod diya')
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
    _col('supplier', True, 'Supplier ka naam (pehle se Suppliers me hona chahiye)', 'ABC Pharma Distributors', 'ABC Pharma Distributors'),
    _col('invoice_number', True, 'Supplier ke bill ka number. Ek bill ki sab rows me same likhein', 'INV-1001', 'INV-1001'),
    _col('product', True, 'Dawa ka naam ya barcode (pehle se Products me hona chahiye)', 'Dolo 650', 'Azithral 500'),
    _col('qty', True, 'Quantity (neeche di unit me)', '20', '10'),
    _col('purchase_price', True, 'Is row ki TOTAL price, GST ke saath (per-piece rate nahi)', '2100', '1850'),
    _col('exp_date', True, 'Expiry: 31/08/2027 ya 08/27', '08/27', '12/26'),
    _col('invoice_date', False, 'Bill ki date (khali = aaj). Ek bill me same rakhein', '05/10/2026', '05/10/2026'),
    _col('unit', False, 'qty kis unit me hai, jaise Strip (khali = product ki base unit)', 'Strip', 'Strip'),
    _col('batch', False, 'Batch number (khali = apne aap ban jayega)', 'B2301', 'AZ778'),
    _col('mfg_date', False, 'Manufacturing date', '09/25', '06/25'),
    _col('rack', False, 'Rack', 'A1', 'A2'),
    _col('row', False, 'Row', '2', '1'),
    _col('location', False, 'Godown / location', '', ''),
]
STOCK_ALIASES = {'supplier_name': 'supplier', 'party': 'supplier', 'invoice': 'invoice_number',
                 'invoice_no': 'invoice_number', 'bill_no': 'invoice_number', 'bill_number': 'invoice_number',
                 'date': 'invoice_date', 'bill_date': 'invoice_date', 'product_name': 'product',
                 'medicine': 'product', 'item': 'product', 'barcode': 'product', 'quantity': 'qty',
                 'price': 'purchase_price', 'total_price': 'purchase_price', 'amount': 'purchase_price',
                 'expiry': 'exp_date', 'expiry_date': 'exp_date', 'exp': 'exp_date', 'mfg': 'mfg_date',
                 'batch_no': 'batch', 'batch_number': 'batch'}


def validate_stock(rows, opts):
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
            errors.append('supplier zaroori hai')
        elif not sup:
            errors.append(f"supplier '{sup_name}' nahi mila (pehle Suppliers import/add karein)")
        invoice = txt(raw.get('invoice_number'))
        if not invoice:
            errors.append('invoice_number zaroori hai')

        pname = txt(raw.get('product'))
        prod = (products_by_code.get(pname) or products_by_name.get(pname.lower())) if pname else None
        if not pname:
            errors.append('product zaroori hai')
        elif not prod:
            errors.append(f"product '{pname}' nahi mila (pehle Products import/add karein)")

        qty = to_int(raw.get('qty'), 'qty', errors, required=True, minimum=1)
        price = to_dec(raw.get('purchase_price'), 'purchase_price', errors, required=True, minimum=Decimal('0'))
        exp = to_date(raw.get('exp_date'), 'exp_date', errors, required=True, month_end=True)
        mfg = to_date(raw.get('mfg_date'), 'mfg_date', errors)
        inv_date = to_date(raw.get('invoice_date'), 'invoice_date', errors) or today
        if mfg and exp and exp < mfg:
            errors.append('exp_date, mfg_date se pehle nahi ho sakti')
        if exp and exp < today:
            warnings.append('ye batch pehle se expire hai')
        if inv_date > today:
            errors.append('invoice_date aage ki nahi ho sakti')

        unit_name = _unit_name(raw.get('unit'))
        unit = None
        if unit_name:
            unit = units.get(unit_name.lower())
            if not unit:
                errors.append(f"unit '{unit_name}' system me nahi hai")
        if prod and not errors:
            try:
                prod.unit_factor(unit)
            except ValidationError as e:
                errors.append(' '.join(e.messages))

        gkey = ((sup.pk if sup else sup_name.lower()), invoice.lower())
        if sup and invoice:
            if (sup.pk, invoice.lower()) in existing_invoices:
                errors.append(f"is supplier ka invoice '{invoice}' pehle se entered hai (double entry se bachne ke liye)")
            first = group_date.setdefault(gkey, (inv_date, n))
            if first[0] != inv_date and txt(raw.get('invoice_date')):
                errors.append(f"is invoice ki date row {first[1]} se alag hai; ek invoice ki ek hi date hogi")
        groups.setdefault(gkey, []).append(len(results))

        data = dict(
            supplier_id=sup.pk if sup else None, supplier=sup.company if sup else sup_name, invoice=invoice,
            invoice_date=inv_date.isoformat(), product_id=prod.pk if prod else None,
            product=prod.name if prod else pname, qty=qty, unit_id=unit.pk if unit else None,
            unit=unit.name if unit else (prod.unit.name if prod and prod.unit_id else ''),
            price=str(price) if price is not None else None, exp=exp.isoformat() if exp else None,
            mfg=mfg.isoformat() if mfg else None, batch=txt(raw.get('batch')),
            rack=txt(raw.get('rack')), row=txt(raw.get('row')), location=txt(raw.get('location')),
            group=f"{gkey[0]}|{gkey[1]}",
        )
        results.append(_result(n, 'error' if errors else 'create', data, errors, warnings,
                               label=f"{invoice} · {data['product']}"))

    # Ek invoice ki koi bhi row galat ho to poora invoice skip (adhura bill stock me na chadhe)
    for idxs in groups.values():
        if any(results[i]['action'] == 'error' for i in idxs):
            first_bad = next(results[i]['n'] for i in idxs if results[i]['action'] == 'error')
            for i in idxs:
                if results[i]['action'] != 'error':
                    results[i]['action'] = 'error'
                    results[i]['errors'].append(f"is invoice ki row {first_bad} me galti hai, isliye poora invoice skip")
    return results


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
                    item = PurchaseItem(
                        purchase=purchase, product_id=d['product_id'], qty=d['qty'], unit_id=d['unit_id'],
                        purchase_price=Decimal(d['price']),
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
    summary['notes'].append(f"{invoices} invoice / purchase ban gaye.")
    return summary


# ======================================================
# EXPENSES
# ======================================================

EXPENSE_COLS = [
    _col('name', True, 'Kharche ka type: Rent, Salary, Electricity, Water, Maintenance ya Other', 'Rent', 'Electricity'),
    _col('amount', True, 'Rupaye', '15000', '2350.50'),
    _col('date', False, 'Date (khali = aaj)', '01/10/2026', '05/10/2026'),
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
            errors.append('name (kharche ka type) zaroori hai')
        elif not code:
            errors.append(f"type '{type_raw}' sahi nahi (Rent, Salary, Electricity, Water, Maintenance ya Other)")
        amount = to_dec(raw.get('amount'), 'amount', errors, required=True)
        if amount is not None and amount <= 0:
            errors.append('amount 0 se zyada hona chahiye')
            amount = None
        when = to_date(raw.get('date'), 'date', errors) or today
        if when > today:
            errors.append('date aage ki nahi ho sakti')
        data = dict(name=code, amount=str(amount) if amount is not None else None, date=when.isoformat())
        if errors:
            action = 'error'
        elif (when, code, amount) in have:
            action = 'skip'
            warnings.append('same date, type aur amount ka kharcha pehle se hai, chhod diya')
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
# CATEGORIES / BRANDS / UNITS  (sirf naam)
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
                errors.append('name zaroori hai')
            elif len(name) > 255:
                errors.append('naam 255 characters se chhota rakhein')
            elif name.lower() in seen:
                errors.append(f"ye naam row {seen[name.lower()]} me bhi hai (file me duplicate)")
            if name:
                seen.setdefault(name.lower(), n)
            if errors:
                action = 'error'
            elif name.lower() in have:
                action = 'skip'
                warnings.append(f'ye {existing_label} pehle se hai, chhod diya')
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

    return dict(cols=[_col('name', True, f'{existing_label.capitalize()} ka naam', *samples)],
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
        desc='Dawaon ki list ek saath. Naam, unit aur GST zaroori hain. Barcode, MRP, pack (Strip/Box) bhi bhar sakte hain.',
        after='Ab Stock Entry se maal ka stock chadhayein.'),
    'customers': dict(
        title='Customers', icon='👥', cols=CUSTOMER_COLS, aliases=CUSTOMER_ALIASES, validate=validate_customers,
        commit=commit_customers, export=export_customers, has_update=True, max_rows=3000,
        desc='Regular customers aur unki udhar limit. Purana udhar baaki yahan nahi bharta, wo bill/receipt se banta hai.',
        after=''),
    'suppliers': dict(
        title='Suppliers', icon='🏭', cols=SUPPLIER_COLS, aliases=SUPPLIER_ALIASES, validate=validate_suppliers,
        commit=commit_suppliers, export=export_suppliers, has_update=True, max_rows=3000,
        desc='Distributors / suppliers ki list. GSTIN dene par state aur PAN apne aap bhar jata hai.',
        after='Stock Entry se pehle suppliers aur products import ho chuke hone chahiye.'),
    'stock': dict(
        title='Stock Entry (Purchases)', icon='📦', cols=STOCK_COLS, aliases=STOCK_ALIASES, validate=validate_stock,
        commit=commit_stock, export=None, has_update=False, max_rows=1500,
        desc='Supplier bills ki rows se purchase aur stock/batch ek saath. Ek invoice ki sab rows me same supplier aur invoice number likhein. '
             'Opening stock ke liye bhi yahi use karein.',
        after='Purchases page par bane hue bills dikhenge. Galti ho to wahan se Purchase Return karein.'),
}
IMPORTERS['expenses'] = dict(
    title='Expenses', icon='💸', cols=EXPENSE_COLS, aliases=EXPENSE_ALIASES, validate=validate_expenses,
    commit=commit_expenses, export=export_expenses, has_update=False, max_rows=3000,
    desc='Kharche (Rent, Salary, Bijli...) ek saath. Same date, type aur amount wali row dobara import nahi hoti.',
    after='Ye kharche Profit & Loss me apne aap ginte jayenge.')
IMPORTERS['categories'] = dict(
    title='Categories', icon='🗂️', desc='Dawa ki categories (Tablet, Syrup, Injection...). Products import me category likhne par apne aap ban bhi jati hai.',
    after='', **_simple_importer(Category, ('Tablet', 'Syrup')))
IMPORTERS['brands'] = dict(
    title='Brands', icon='🏷️', desc='Brand / company ke naam (Cipla, Sun Pharma...). Products import me brand likhne par apne aap ban bhi jata hai.',
    after='', **_simple_importer(Brand, ('Cipla', 'Sun Pharma')))
IMPORTERS['units'] = dict(
    title='Units', icon='📏', desc='Units (Tablet, Strip, Box, Bottle...). Chhote naam (tab, strips, btl) apne aap sahi naam me badal jate hain.',
    after='Strip/Box jaise bade pack ka conversion Products import ke pack1/pack2 columns se set hota hai.',
    **_simple_importer(Unit, ('Tablet', 'Strip'), alias_units=True))
IMPORTERS['stock']['desc_extra'] = 'Ek invoice me ek bhi row galat ho to poora invoice skip hota hai.'


def canonical_columns(imp, headers):
    """File ke headers ko importer ke standard column naam se map karo. (mapped_rows ke liye)"""
    names = {c['name'] for c in imp['cols']}
    out = {}
    for h in headers:
        key = h if h in names else imp['aliases'].get(h)
        if key and key not in out.values():
            out[h] = key
    return out


def prepare_rows(imp, headers, rows):
    """Aliases ke hisaab se header badal kar rows ko standard naam wali dict me badalta hai."""
    mapping = canonical_columns(imp, headers)
    missing = [c['name'] for c in imp['cols'] if c['required'] and c['name'] not in mapping.values()]
    ignored = [h for h in headers if h and h not in mapping]
    mapped = [(n, {mapping[h]: v for h, v in raw.items() if h in mapping}) for n, raw in rows]
    return mapped, missing, ignored


# ======================================================
# Temp store (preview -> confirm ke beech)
# ======================================================

STORE = Path(tempfile.gettempdir()) / 'medsync_imports'
_TOKEN = re.compile(r'^[0-9a-f]{32}$')


def stash(payload):
    STORE.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for f in STORE.glob('*.json'):          # purani files saaf
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    token = secrets.token_hex(16)
    (STORE / f'{token}.json').write_text(json.dumps(payload), encoding='utf-8')
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
# Download helpers (import page + har list page ka Export)
# ======================================================

_NUM = re.compile(r'^-?\u20b9?\s?(0|[1-9]\d{0,8})(\.\d+)?$')


def export_value(v):
    """List page ka text ('₹1,250.00', '12') Excel me number ban kar jaye, taaki sum/sort kar sakein."""
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
    heads = [c['name'] for c in imp['cols']]
    required = {c['name'] for c in imp['cols'] if c['required']}
    data = build_xlsx([
        dict(title='Data', headers=heads, rows=[], required=required),
        dict(title='Example', headers=heads, rows=[list(c['samples'][i] for c in imp['cols']) for i in (0, 1)],
             required=required),
        dict(title='Help', headers=['Column', 'Zaroori?', 'Kya likhein'],
             rows=[[c['name'], 'HAAN' if c['required'] else 'optional', c['help']] for c in imp['cols']]),
    ])
    resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="medsync_{kind}_template.xlsx"'
    return resp
