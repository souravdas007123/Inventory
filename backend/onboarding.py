"""Getting a new shop ready: the 'Get your shop ready' card on the owner's dashboard and the Setup guide page.

Every step is worked out from the shop's own data, so it ticks itself. Nothing here writes anything.
A helper like this must never break a page: each part catches its own problems."""
import logging

from .models import Bill, Customer, Product, Purchase, ShopProfile, Supplier

log = logging.getLogger(__name__)


def steps():
    """The five first steps, in the order that works: [{'key', 'title', 'text', 'url', 'done', 'why', 'tips', 'buttons'}].
    `text` is the one-liner for the dashboard card; `why`, `tips` and `buttons` are for the Setup guide page."""
    profile = ShopProfile.objects.first()
    return [
        dict(key='shop', title='Add your shop details', url='/shop/',
             text='Name, address, phone and GSTIN are printed on every bill.',
             why='Your customers and the tax office see these details on every bill you print.',
             tips=['Fill in the address and phone, then your GSTIN and Drug Licence number.',
                   'Check the spelling: a bill cannot be edited after it is final.'],
             buttons=[('Open Shop Settings', '/shop/')],
             done=bool(profile and profile.address.strip() and profile.phone.strip())),
        dict(key='suppliers', title='Add your suppliers', url='/import/suppliers/',
             text='The companies you buy from. Each purchase is entered under one of them.',
             why='Every purchase belongs to a supplier. That is how MedSync knows what you owe and who to send expired stock back to.',
             tips=['Import them all from Excel, or add them one by one.',
                   'Add the phone number: it is used to send purchase orders and expiry-return requests on WhatsApp.'],
             buttons=[('Import suppliers', '/import/suppliers/'), ('Download the template', '/import/suppliers/?template=1'),
                      ('Add one', '/suppliers/new/')],
             done=Supplier.objects.exists()),
        dict(key='products', title='Add your medicines', url='/import/',
             text='Import your product list from Excel. Medicines are also created when you enter stock.',
             why='Bills and stock are made from your product list. Units and categories (Tablet, Strip, Syrup ...) are already there for you.',
             tips=['Import the whole list from Excel. A preview shows any mistakes before anything is saved.',
                   'Give every medicine its unit, GST rate and MRP: bills take the rate from the MRP.',
                   'Medicines cannot be added one by one on the Products page. They are created by this import, or by the stock entry in the next step '
                   '(the stock import creates a medicine it does not find yet). So if you have your stock in Excel, you can do both steps at once.'],
             buttons=[('Import products', '/import/products/'), ('Download the template', '/import/products/?template=1')],
             done=Product.objects.exists()),
        dict(key='stock', title='Enter your stock', url='/purchases/new/',
             text='Enter a purchase so each medicine has a batch with its expiry date.',
             why='Stock exists only through purchases, and every batch needs its batch number and expiry date. That is what makes expiry alerts, FEFO billing and returns work.',
             tips=['Opening stock: enter what is on your shelves now as a purchase, or import it from Excel.',
                   'Never skip the expiry date. Without it a batch cannot warn you or be returned to the supplier.',
                   'One row per medicine and batch. Repeat the supplier and invoice number for the medicines of the same invoice.'],
             buttons=[('Import stock', '/import/stock/'), ('Download the template', '/import/stock/?template=1'),
                      ('Enter a purchase', '/purchases/new/')],
             done=Purchase.objects.exists()),
        dict(key='bill', title='Make your first bill', url='/bills/new/',
             text='Scan or search a medicine, press F9 to save, and print.',
             why='A first bill shows you that everything works: the rate, the GST, the batch that is sold and the stock that goes down.',
             tips=['Scan the barcode or type the name, press Enter to move on, and F9 to save.',
                   'Use "Share on WhatsApp" on the bill list to send the bill to the customer.'],
             buttons=[('New bill', '/bills/new/')],
             done=Bill.objects.filter(status='FINAL').exists()),
    ]


def checklist(user, hidden=False):
    """The steps to show to this user, or None when nothing should be shown (not the owner, hidden, or all done).
    `fresh` is True for a shop where nothing is done yet (a brand new shop): it gets the welcome note once."""
    from . import access
    if hidden or not access.is_owner(user):
        return None
    try:
        items = steps()
    except Exception:                      # a helper card must never break the dashboard
        log.exception('Could not work out the setup steps')
        return None
    if all(i['done'] for i in items):
        return None
    done = sum(1 for i in items if i['done'])
    return dict(items=items, done=done, total=len(items), percent=round(100 * done / len(items)), fresh=done == 0)


def guide(user):
    """Everything the Setup guide page shows. Each part is separate: one failing part leaves the others working."""
    out = dict(steps=[], done=0, total=0, percent=0, extras=[], checks=[], problem=False)
    try:
        items = steps()
        out.update(steps=items, done=sum(1 for i in items if i['done']), total=len(items))
        out['percent'] = round(100 * out['done'] / out['total']) if out['total'] else 0
    except Exception:
        log.exception('Could not work out the setup steps')
        out['problem'] = True
    out['extras'] = extras(user)
    out['checks'] = data_checks()
    return out


def extras(user):
    """'Also worth doing': [{'title', 'text', 'url', 'link', 'done'}]; done is None when it cannot be known."""
    out = []

    def add(title, text, url, link, test):
        try:
            done = None if test is None else bool(test())          # no test: it cannot be known from the data
        except Exception:
            log.exception('Setup extra %s failed', title)
            done = None
        out.append(dict(title=title, text=text, url=url, link=link, done=done))

    profile = ShopProfile.objects.first()
    add('GSTIN and Drug Licence number', 'Both belong on every bill. Add them in Shop Settings.', '/shop/', 'Open Shop Settings',
        lambda: profile and profile.gstin.strip() and profile.drug_license.strip())
    add('Customers who buy on credit', 'Add them with their old balance, so their credit account starts correct.',
        '/import/customers/', 'Import customers', lambda: Customer.objects.exists())
    add('What you still owe your suppliers', 'If you owe a supplier from before, enter it on the supplier. The Ledger then starts correct.',
        '/suppliers/', 'Open Suppliers', lambda: Supplier.objects.exclude(opening_balance=0).exists())

    def staff():
        from django.contrib.auth import get_user_model
        return get_user_model().objects.filter(is_active=True).count() > 1
    add('Logins for your staff', 'Give each person their own login and role, so billing staff see only billing.', '/users/', 'Open Users', staff)

    def two_step():
        from .models import TwoFactor
        return TwoFactor.objects.filter(user_id=user.pk).exists()
    add('Turn on 2-step sign-in', 'A code by e-mail after the password keeps your shop safe even if the password leaks.',
        '/security/', 'Open Sign-in security', two_step)
    add('Download a backup copy', 'A backup is taken every day. Keep one copy of your own as well.', '/backup/', 'Open Backup', None)
    return out


def data_checks():
    """Mistakes in the data that make trouble later: [{'title', 'text', 'count', 'url', 'link'}] (count is None when it could not be read)."""
    from .models import Batch
    out = []

    def add(title, text, url, link, query):
        try:
            count = query()
        except Exception:
            log.exception('Data check %s failed', title)
            count = None
        out.append(dict(title=title, text=text, count=count, url=url, link=link))

    add('Medicines without a unit', 'Stock and bills cannot count a medicine that has no unit.', '/products/', 'Open Products',
        lambda: Product.objects.filter(unit__isnull=True).count())
    add('Medicines without an MRP', 'Bills take the selling rate from the MRP, so it would have to be typed on every bill.', '/products/',
        'Open Products', lambda: Product.objects.filter(mrp__isnull=True).exclude(is_active=False).count())
    add('Batches with stock but no expiry date', 'Without an expiry date a batch cannot warn you or be returned to the supplier.', '/batches/',
        'Open Batch and Expiry', lambda: Batch.objects.filter(qty__gt=0, expire_date__isnull=True).count())
    add('Suppliers without a phone number', 'Purchase orders and expiry-return requests go out on WhatsApp, which needs the number.',
        '/suppliers/', 'Open Suppliers', lambda: Supplier.objects.filter(phone__isnull=True).count() + Supplier.objects.filter(phone='').count())
    return out
