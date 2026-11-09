"""Barcode labels for products: print them on an A4 sheet or a label roll, and make a shop code for a product that has none."""
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import access, barcode
from .models import Product, Purchase, ShopProfile

MAX_PRODUCTS, MAX_COPIES = 200, 200
SIZES = {
    'a4x24': ('A4 sheet, 24 labels (3 x 8)', 70, 37),
    'a4x40': ('A4 sheet, 40 labels (4 x 10)', 52.5, 29.7),
    'roll': ('Label roll 50 x 25 mm', 50, 25),
}


def _ids(raw):
    out = []
    for part in str(raw or '').split(','):
        part = part.strip()
        if part.isdecimal() and int(part) not in out:
            out.append(int(part))
    return out[:MAX_PRODUCTS]


def _copies(raw, default=1):
    try:
        return max(0, min(MAX_COPIES, int(raw)))
    except (TypeError, ValueError):
        return default


def _products(request):
    """[(product, copies)] for the ids in the address, or the products of one purchase (copies = the quantity bought)."""
    pairs = []
    pur = request.GET.get('purchase', '')
    if pur.isdecimal():
        purchase = Purchase.objects.filter(pk=int(pur)).prefetch_related('items__product').first()
        if purchase:
            seen = {}
            for it in purchase.items.all():
                if it.product_id:
                    seen[it.product_id] = (it.product, seen.get(it.product_id, (None, 0))[1] + int(it.qty or 0))
            pairs = [(p, n) for p, n in seen.values()]
    else:
        ids = _ids(request.GET.get('ids'))
        found = {p.pk: p for p in Product.objects.filter(pk__in=ids)}
        pairs = [(found[i], 1) for i in ids if i in found]
    out = []
    for p, default in pairs[:MAX_PRODUCTS]:
        key = f'n{p.pk}'
        out.append((p, _copies(request.GET.get(key), default) if key in request.GET else _copies(default, 1)))
    return out


@access.perm_required(('view', 'product'))
def labels(request):
    size = request.GET.get('size') if request.GET.get('size') in SIZES else 'a4x24'
    rows, sheet = [], []
    shop = ShopProfile.get().name
    for p, n in _products(request):
        code = (p.barcode or '').strip()
        state = 'ok' if barcode.kind(code) else ('none' if not code else 'other')
        rows.append(dict(p=p, n=n, state=state, code=code))
        if state == 'ok':
            art = barcode.svg(code)
            sheet += [dict(name=(p.name or '')[:60], mrp=f'{p.mrp:.2f}' if p.mrp is not None else '', svg=art, shop=shop)] * n
    keep = ','.join(str(r['p'].pk) for r in rows)
    return render(request, 'app/labels.html', dict(
        active='products', rows=rows, sheet=sheet, size=size, sizes=SIZES, dim=SIZES[size], keep=keep,
        purchase=request.GET.get('purchase', '') if request.GET.get('purchase', '').isdecimal() else '',
        missing=sum(1 for r in rows if r['state'] == 'none'), total=len(sheet),
        can_make=access.can(request.user, 'change', 'product'), here=request.get_full_path()))


@require_POST
@access.perm_required(('change', 'product'))
def label_codes(request):
    """Give every listed product that has NO barcode a shop code. A product that already has a barcode is never touched."""
    made = 0
    for p in Product.objects.filter(Q(barcode__isnull=True) | Q(barcode=''), pk__in=_ids(request.POST.get('ids'))).order_by('pk'):
        n = p.pk
        for _ in range(5):                                  # the code is taken by another product only in a very rare case
            code = barcode.shop_code(n)
            try:
                with transaction.atomic():
                    if Product.objects.filter(barcode=code).exists():
                        raise IntegrityError
                    Product.objects.filter(Q(barcode__isnull=True) | Q(barcode=''), pk=p.pk).update(barcode=code)
                made += 1
                break
            except IntegrityError:
                n += 1_000_000
    messages.success(request, f'{made} barcode(s) created.' if made else 'Nothing to create.')
    nxt = request.POST.get('next', '')
    if not (nxt.startswith('/labels/') and '//' not in nxt):
        nxt = '/labels/'
    return HttpResponseRedirect(nxt)
