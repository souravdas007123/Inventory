"""Reset data: removes the shop's data in one safe step so that fresh data can be entered.

Groups (the order of deleting matters because some links are protected):
  transactions : bills, purchases, returns, payments, receipts, expenses, orders, write-offs, stock audits, batches
  medicines    : products, brands, categories, units (needs transactions to be removed first)
  parties      : suppliers and customers (needs transactions to be removed first)
  staff        : employees and fixed expenses (needs transactions to be removed first)

Never removed: Shop Settings, user logins and permissions, the audit log, saved import layouts and blog posts.
"""
from decimal import Decimal

from django.db import transaction

from .models import (Batch, Bill, Brand, Category, Customer, CustomerPayment, DocCounter, Employee, Expense,
                     HealthSnapshot, HeldBill, InvoiceCounter, Order, Payment, Product, Purchase,
                     PurchaseOrder, PurchaseReturn, RecurringExpense, Sale, SalesReturn, StockAudit, StockWriteOff,
                     Supplier, Transaction, Unit)

DEFAULT_CATEGORIES = ('Tablet', 'Capsule', 'Syrup', 'Injection', 'Ointment / Cream', 'Drops', 'Powder', 'Surgical', 'General')
DEFAULT_UNITS = ('Tablet', 'Capsule', 'Strip', 'Bottle', 'Tube', 'Box', 'Piece')

GROUPS = (
    ('transactions', 'Bills, purchases and money records',
     'All bills, purchases, returns, supplier payments, customer receipts, expenses, orders, write-offs, stock audits and held bills. '
     'Stock of every medicine becomes 0 and invoice numbers start again from 1.',
     False),
    ('medicines', 'Medicines and master data',
     'All products with their batches and unit conversions, plus brands, categories and units. '
     'The common units (Tablet, Strip, Box ...) and categories (Tablet, Syrup, Injection ...) are added back so you can start at once.',
     True),
    ('parties', 'Suppliers and customers',
     'All suppliers and all customers (their credit balances go with them).',
     True),
    ('staff', 'Employees and fixed expenses',
     'All employees, salary settings and monthly fixed expenses.',
     True),
)
KEYS = tuple(k for k, *_ in GROUPS)


def normalise(keys):
    """Keeps only known groups, in the fixed order. Every other group needs the transactions to go first."""
    picked = {k for k in (keys or ()) if k in KEYS}
    if picked:
        picked.add('transactions')
    return [k for k in KEYS if k in picked]


def counts():
    """What would be removed, per group: {key: [(label, number), ...]}."""
    return {
        'transactions': [('Bills', Bill.objects.count()), ('Purchases', Purchase.objects.count()),
                         ('Expenses', Expense.objects.count()),
                         ('Payments and receipts', Payment.objects.count() + CustomerPayment.objects.count())],
        'medicines': [('Products', Product.objects.count()), ('Brands', Brand.objects.count()),
                      ('Categories', Category.objects.count()), ('Units', Unit.objects.count())],
        'parties': [('Suppliers', Supplier.objects.count()), ('Customers', Customer.objects.count())],
        'staff': [('Employees', Employee.objects.count()), ('Fixed expenses', RecurringExpense.objects.count())],
    }


def _transactions():
    # leaf records first: several links to them are protected, so the order below matters
    for model in (SalesReturn, PurchaseReturn, StockWriteOff, StockAudit, Payment, CustomerPayment, PurchaseOrder,
                  HeldBill, Bill, Purchase, Order, Expense, Sale, Transaction, Batch, HealthSnapshot,
                  InvoiceCounter, DocCounter):
        model.objects.all().delete()
    Product.objects.update(stock_qty=0, batch=None)
    Supplier.objects.update(opening_balance=Decimal('0.00'))          # the amount owed to a supplier is kept on the supplier


def _medicines():
    Product.objects.all().delete()          # unit conversions go with the products
    for model in (Brand, Category, Unit):
        model.objects.all().delete()
    for name in DEFAULT_UNITS:
        Unit.objects.create(name=name)
    for name in DEFAULT_CATEGORIES:
        Category.objects.create(name=name)


def _parties():
    Supplier.objects.all().delete()
    Customer.objects.all().delete()


def _staff():
    Employee.objects.all().delete()
    RecurringExpense.objects.all().delete()


STEPS = {'transactions': _transactions, 'medicines': _medicines, 'parties': _parties, 'staff': _staff}


def run(keys):
    """Removes the chosen groups (and the ones they depend on) in ONE database transaction:
    if anything fails, nothing is removed. Returns the list of group keys that were reset."""
    keys = normalise(keys)
    with transaction.atomic():
        for k in keys:
            STEPS[k]()
    return keys


def log_entry(request, keys, backup_name):
    from . import auditlog
    auditlog.record(request, 'RESET', 'All data', f"Removed: {', '.join(keys)}. Backup: {backup_name}")


__all__ = ['GROUPS', 'KEYS', 'DEFAULT_UNITS', 'normalise', 'counts', 'run', 'log_entry']
