"""Shared set-up for the tests: a supplier, a product (Tablet, 1 Strip = 10 Tablets, 12% GST) and small helpers
to buy stock and to sell it, the same way the app does it (through the models)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from backend.models import Bill, InvoiceItem, Product, ProductUnit, Purchase, PurchaseItem, Supplier, Unit


def today():
    return timezone.localdate()


class StockMixin:
    def setUp(self):
        super().setUp()
        self.tablet = Unit.objects.create(name='Tablet')
        self.strip = Unit.objects.create(name='Strip')
        self.supplier = Supplier.objects.create(company='ABC Pharma')
        self.product = Product.objects.create(name='Dolo 650', unit=self.tablet, gst_rate=Decimal('12.00'))
        ProductUnit.objects.create(product=self.product, unit=self.strip, factor=10)

    def buy(self, qty=5, price='560.00', unit='strip', days=365, invoice='INV-1', product=None, selling=None):
        """One purchase invoice with one item. Default: 5 Strips (= 50 Tablets) for Rs 560 incl. 12% GST."""
        unit = {'strip': self.strip, 'tablet': self.tablet}.get(unit, unit) if isinstance(unit, str) else unit
        purchase = Purchase.objects.create(supplier=self.supplier, invoice_number=invoice, invoice_date=today())
        item = PurchaseItem.objects.create(
            purchase=purchase, product=product or self.product, qty=qty, unit=unit, purchase_price=Decimal(price),
            selling_price=Decimal(selling) if selling else None, expire_date=today() + timedelta(days=days))
        return purchase, item

    def new_bill(self, **kw):
        kw.setdefault('customer_name', 'Cash')
        kw.setdefault('date', today())
        return Bill.objects.create(**kw)

    def sell(self, qty=10, rate='10.00', unit=None, bill=None, product=None):
        """Sell `qty` (in `unit`, default the base unit) at `rate` per unit before GST."""
        bill = bill or self.new_bill()
        item = InvoiceItem.objects.create(bill=bill, product=product or self.product, qty=qty,
                                          rate=Decimal(rate), unit=unit)
        return bill, item

    def stock(self):
        return Product.objects.get(pk=self.product.pk).stock_qty
