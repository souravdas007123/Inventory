"""The 'get started' checklist on the owner's dashboard: shop details, products, stock, first bill.
Every step is worked out from the data, so it ticks itself and disappears when the shop is set up."""
from .models import Bill, Product, Purchase, ShopProfile


def steps():
    """[{'key', 'title', 'text', 'url', 'done'}] for the four first steps."""
    profile = ShopProfile.objects.first()
    return [
        dict(key='shop', title='Add your shop details', url='/shop/',
             text='Name, address, phone and GSTIN are printed on every bill.',
             done=bool(profile and profile.address.strip() and profile.phone.strip())),
        dict(key='products', title='Add your medicines', url='/import/',
             text='Import your product list from Excel, or add products one by one.',
             done=Product.objects.exists()),
        dict(key='stock', title='Enter your stock', url='/purchases/new/',
             text='Enter a purchase so each medicine has a batch with its expiry date.',
             done=Purchase.objects.exists()),
        dict(key='bill', title='Make your first bill', url='/bills/new/',
             text='Scan or search a medicine, press F9 to save, and print.',
             done=Bill.objects.filter(status='FINAL').exists()),
    ]


def checklist(user, hidden=False):
    """The steps to show to this user, or None when nothing should be shown (not the owner, hidden, or all done)."""
    from . import access
    if hidden or not access.is_owner(user):
        return None
    try:
        items = steps()
    except Exception:                      # a helper card must never break the dashboard
        return None
    if all(i['done'] for i in items):
        return None
    done = sum(1 for i in items if i['done'])
    return dict(items=items, done=done, total=len(items), percent=round(100 * done / len(items)))
