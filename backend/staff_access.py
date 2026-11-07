"""Staff and Access: what a staff member may do, in the words of the shop (not Django permission names).

The owner ticks View / Add / Edit / Delete for each AREA of the shop (Billing, Customers, Products and stock, ...). Each tick is
turned into the normal Django model permissions that the rest of the app already checks (backend/access.py), so nothing about
how the app decides who may do what changes. Roles are only ready-made sets of ticks.

What a staff member can NEVER be given here: the owner-only pages (reports and profit, GST, import / export, audit log, backup,
shop settings, staff and access) and the Django admin panel. Those stay with the owner.
"""
from django.contrib.auth.models import Permission

ACTIONS = ('view', 'add', 'change', 'delete')
ACTION_LABELS = {'view': 'View', 'add': 'Add', 'change': 'Edit', 'delete': 'Delete'}

# key, title, what it is, models (the first one decides what the grid shows), sensitive (needs a warning when it can change data)
AREAS = [
    dict(key='billing', title='Billing', hint='Make, print and look up bills; customer orders',
         models=['bill', 'invoiceitem', 'order', 'sale', 'heldbill', 'offlinebill'], sensitive=False),
    dict(key='customers', title='Customers', hint='Customer list, credit dues, receipts, sales returns',
         models=['customer', 'customerpayment', 'salesreturn', 'salesreturnitem'], sensitive=False),
    dict(key='stock', title='Products and stock', hint='Medicines, batches, categories, brands, units, write-offs',
         models=['product', 'productunit', 'batch', 'category', 'brand', 'unit', 'stockwriteoff'], sensitive=True),
    dict(key='returns', title='Expiry and purchase returns', hint='Near-expiry list, returns to suppliers (debit notes)',
         models=['purchasereturn', 'purchasereturnitem'], sensitive=False),
    dict(key='purchases', title='Purchases and suppliers', hint='Purchase bills, purchase orders, suppliers, supplier payments',
         models=['purchase', 'purchaseitem', 'purchaseorder', 'purchaseorderitem', 'supplier', 'payment'], sensitive=True),
    dict(key='expenses', title='Expenses and salary', hint='Expenses, fixed expenses, employees, salary sheet, transactions',
         models=['expense', 'recurringexpense', 'employee', 'transaction'], sensitive=True),
]
AREA_BY_KEY = {a['key']: a for a in AREAS}

# Only the owner can open these (see access.NAV_RULES). Shown in the screen so nobody looks for a tick that does not exist.
OWNER_ONLY = ['Profit & Loss, GST and CA reports', 'Import / Export', 'Audit log and sign-in history', 'Backup',
              'Shop settings', 'Staff and access']

# role -> {area: actions}. "view" is implied by any other action.
ROLES = [
    ('cashier', 'Cashier', 'Billing counter', {'billing': 'vac', 'customers': 'vac', 'stock': 'v'}),
    ('pharmacist', 'Pharmacist', 'Bills, stock and expiry',
     {'billing': 'vac', 'customers': 'vac', 'stock': 'vac', 'returns': 'vac', 'purchases': 'v'}),
    ('purchase', 'Purchase manager', 'Buying and suppliers',
     {'billing': 'v', 'customers': 'v', 'stock': 'vac', 'returns': 'vac', 'purchases': 'vacd'}),
    ('accountant', 'Accountant', 'Money and expenses',
     {'billing': 'v', 'customers': 'v', 'stock': 'v', 'purchases': 'v', 'expenses': 'vacd'}),
    ('manager', 'Manager', 'Everything staff can have',
     {'billing': 'vac', 'customers': 'vac', 'stock': 'vac', 'returns': 'vac', 'purchases': 'vacd', 'expenses': 'vacd'}),
]
ROLE_BY_KEY = {r[0]: r for r in ROLES}
_LETTER = {'v': 'view', 'a': 'add', 'c': 'change', 'd': 'delete'}


def empty_grid():
    return {a['key']: set() for a in AREAS}


def normalise(grid):
    """Keep only known areas / actions, and make 'view' part of every other action (you cannot edit what you cannot see)."""
    out = empty_grid()
    for key, actions in (grid or {}).items():
        if key in out:
            acts = {a for a in actions if a in ACTIONS}
            if acts:
                acts.add('view')
            out[key] = acts
    return out


def role_grid(role_key):
    out = empty_grid()
    role = ROLE_BY_KEY.get(role_key)
    if role:
        for area, letters in role[3].items():
            out[area] = {_LETTER[c] for c in letters}
    return normalise(out)


def role_of(grid):
    """The name of the role these ticks match exactly, 'Custom' for any other mix, 'No access' for nothing."""
    grid = normalise(grid)
    if not any(grid.values()):
        return 'No access'
    for key, title, _hint, _areas in ROLES:
        if role_grid(key) == grid:
            return title
    return 'Custom'


def codenames(grid):
    """The Django permission codenames (app.codename without the app) that these ticks stand for."""
    names = set()
    for key, actions in normalise(grid).items():
        for model in AREA_BY_KEY[key]['models']:
            for act in actions:
                names.add(f'{act}_{model}')
    return names


def _permissions(names):
    return list(Permission.objects.filter(content_type__app_label='backend', codename__in=names))


def grid_from(have):
    """Ticks for a set of permission codenames (backend app). A tick is shown when the area's main model has that permission."""
    out = empty_grid()
    for area in AREAS:
        main = area['models'][0]
        for act in ACTIONS:
            if f'{act}_{main}' in have:
                out[area['key']].add(act)
    return normalise(out)


def _own_codenames(user):
    return {p.split('.', 1)[1] for p in user.get_all_permissions() if p.startswith('backend.')}


def grid_of(user):
    """What this user can do now, as ticks."""
    return grid_from(_own_codenames(user))


def codenames_for_users(users):
    """{user id: set of backend permission codenames} for many users at once (their own permissions and their groups'), in 2 queries."""
    from collections import defaultdict
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    ids = [u.pk for u in users]
    out = defaultdict(set)
    User = get_user_model()
    direct = User.user_permissions.through.objects.filter(user_id__in=ids, permission__content_type__app_label='backend')
    for uid, code in direct.values_list('user_id', 'permission__codename'):
        out[uid].add(code)
    member = defaultdict(set)
    for uid, gid in User.groups.through.objects.filter(user_id__in=ids).values_list('user_id', 'group_id'):
        member[gid].add(uid)
    if member:
        gp = Group.permissions.through.objects.filter(group_id__in=list(member), permission__content_type__app_label='backend')
        for gid, code in gp.values_list('group_id', 'permission__codename'):
            for uid in member[gid]:
                out[uid].add(code)
    return out


def extra_permissions(user):
    """How many permissions this user has that the screen cannot show (set earlier in the Django admin, or through a group).
    Saving from the screen replaces them."""
    have = set(user.get_all_permissions())
    wanted = {f'backend.{c}' for c in codenames(grid_of(user))}
    return len(have - wanted)


def apply(user, grid):
    """Give the user exactly these ticks: their own permission list becomes the matching model permissions, and the groups
    they were in are cleared (a group would keep handing out permissions the screen does not show)."""
    user.user_permissions.set(_permissions(codenames(grid)))
    user.groups.clear()


def summary(grid):
    """Plain-English lines: what the user can do, and where they have no access."""
    grid = normalise(grid)
    can, cannot = [], []
    for area in AREAS:
        acts = grid[area['key']]
        if acts:
            can.append((area['title'], [ACTION_LABELS[a].lower() for a in ACTIONS if a in acts]))
        else:
            cannot.append(area['title'])
    return can, cannot


def risky(grid):
    """Areas where the user may change or delete important data (shown as a warning)."""
    grid = normalise(grid)
    out = []
    for area in AREAS:
        acts = grid[area['key']]
        if 'delete' in acts or (area['sensitive'] and acts - {'view'}):
            out.append(area['title'] + (' (delete)' if 'delete' in acts else ''))
    return out
