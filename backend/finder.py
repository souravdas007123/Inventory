"""Medicine Finder: type a medicine or a salt and see every medicine with the same composition, cheapest first.

Same rules as the substitute suggestion on the bill (`Product.get_alternatives`): the composition must be exactly the same
(same salts, same strengths). Medicines with no composition written in Products have no substitutes, so the page says so."""
import re

from .models import Product, norm_composition

MAX_MATCHES = 5
MAX_SUBSTITUTES = 30


def substitutes(product):
    """Active medicines with the same composition as `product` (not itself): in stock first, then cheapest MRP, then name."""
    key = norm_composition(product.composition)
    word = re.search(r'[a-z]{4,}', (product.composition or '').lower())
    if not key or not word:
        return []
    cands = (Product.objects.exclude(is_active=False).exclude(pk=product.pk).filter(composition__icontains=word.group(0))
             .select_related('unit').prefetch_related('batches', 'unit_conversions__unit')[:300])
    same = [c for c in cands if norm_composition(c.composition) == key]
    for c in same:
        c.sellable = c.available_qty()
    same.sort(key=lambda c: (c.sellable <= 0, c.mrp is None, c.mrp or 0, c.name or ''))
    return same[:MAX_SUBSTITUTES]


def find(q):
    """[{'product', 'subs'}] for the medicines matching the text (name, composition or barcode)."""
    q = (q or '').strip()[:100]
    if not q:
        return []
    from django.db.models import Q
    from . import barcode
    qs = (Product.objects.exclude(is_active=False).filter(Q(name__icontains=q) | Q(composition__icontains=q) | Q(barcode__in=barcode.variants(q)))
          .select_related('unit').prefetch_related('batches', 'unit_conversions__unit').order_by('name')[:MAX_MATCHES])
    out = []
    for p in qs:
        p.sellable = p.available_qty()
        out.append(dict(product=p, subs=substitutes(p)))
    return out
