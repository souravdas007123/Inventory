from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
import random
import string

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

TWO = Decimal('0.01')
RATE_Q = Decimal('0.0001')   # per base unit rate 4 decimal tak (sasti tablets ka COGS sahi rahe)

GST_STATE_CODES = {
    '01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
    '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
    '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
    '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram',
    '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
    '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
    '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '27': 'Maharashtra', '29': 'Karnataka', '32': 'Kerala',
    '33': 'Tamil Nadu', '36': 'Telangana', '37': 'Andhra Pradesh'
}


# ======================================================
# HELPERS (ID generators)
# ======================================================

def _random_code(length):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def generate_unique_sku_id():
    return f"SKU-{_random_code(4)}"


def generate_unique_order_id():
    return f"SOFY-{_random_code(6)}"


def generate_unique_batch_number():
    new_id = f"BATCH-{_random_code(6)}"
    while Batch.objects.filter(batch_number=new_id).exists():
        new_id = f"BATCH-{_random_code(6)}"
    return new_id


def generate_po_number():
    return f"PO-{_random_code(6)}"


def adjust_supplier_balance(supplier_id, delta):
    """Supplier ka balance (payable) delta se badhata/ghatata hai."""
    if not supplier_id or not delta:
        return
    delta = Decimal(str(delta))
    supplier = Supplier.objects.select_for_update().get(pk=supplier_id)
    supplier.opening_balance = (supplier.opening_balance or Decimal('0')) + delta
    supplier.save(update_fields=['opening_balance'])


def resolve_unit_factor(instance, old):
    """
    Row (PurchaseItem / InvoiceItem) ka unit factor.
    Same product + same unit par edit ho to purana snapshot hi use hota hai,
    taaki baad me conversion badalne se purane records ki qty na bigde.
    """
    if old and old.product_id == instance.product_id and old.unit_id == instance.unit_id:
        return old.unit_factor or 1
    return instance.product.unit_factor(instance.unit)


# ======================================================
# MASTER DATA
# ======================================================

class Category(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "01. Category"
        verbose_name_plural = "01. Category"


class Brand(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "02. Brand"
        verbose_name_plural = "02. Brand"


class Unit(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "03. Unit"
        verbose_name_plural = "03. Unit"


class Supplier(models.Model):
    company = models.CharField(max_length=200, unique=True, default="ABC Supplier")
    person = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True, verbose_name="Phone Number")
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=50, blank=True, null=True)
    state = models.CharField(max_length=50, editable=False, blank=True, null=True)
    gstin = models.CharField(max_length=15, unique=True, help_text="Enter 15 digit GSTIN", blank=True, null=True)
    pan = models.CharField(max_length=10, editable=False, blank=True, null=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, editable=False, blank=True, null=True,
                                          verbose_name="Balance")
    created_at = models.DateField(auto_now_add=True, blank=True, null=True)
    updated_at = models.DateField(auto_now=True, blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Active Status")

    def clean(self):
        if self.gstin == "":
            self.gstin = None
        if self.gstin and len(self.gstin) != 15:
            raise ValidationError({'gstin': 'GSTIN 15 characters ka hona chahiye.'})

    def save(self, *args, **kwargs):
        if self.gstin == "":
            self.gstin = None
        if self.gstin and len(self.gstin) == 15:
            self.gstin = self.gstin.upper()
            self.pan = self.gstin[2:12]
            self.state = GST_STATE_CODES.get(self.gstin[0:2], "Unknown State")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.company

    class Meta:
        verbose_name = "04. Supplier"
        verbose_name_plural = "04. Supplier"


# ======================================================
# PRODUCT
# ======================================================

class Product(models.Model):
    GST_CHOICES = (
        (Decimal('0.00'), '0% (Nil / Exempt)'),
        (Decimal('5.00'), '5%'),
        (Decimal('12.00'), '12%'),
        (Decimal('18.00'), '18%'),
    )
    name = models.CharField(max_length=255, blank=True, null=True)
    composition = models.CharField(max_length=255, blank=True, null=True, help_text="Example: Paracetamol 500mg")
    hsn_code = models.CharField(max_length=10, blank=True, null=True, verbose_name="HSN / SAC")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.CharField(max_length=50, editable=False, blank=True, null=True)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True)
    sku = models.CharField(max_length=50, unique=True, editable=False, blank=True, null=True)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('18.00'), choices=GST_CHOICES, verbose_name="GST (%)")
    stock_qty = models.PositiveIntegerField(editable=False, default=0, verbose_name="Stock")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Base Unit",
                             help_text="Sabse chhoti unit (e.g. Tablet). Stock hamesha isi me store hota hai.")
    reorder_level = models.PositiveIntegerField(default=5, help_text="Base unit me")
    default_supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Default Supplier for PO")
    location = models.CharField(max_length=100, blank=True, null=True, verbose_name="Location/Godown")
    rack = models.CharField(max_length=50, blank=True, null=True, verbose_name="Rack No.")
    row = models.CharField(max_length=50, blank=True, null=True, verbose_name="Row No.")
    is_active = models.BooleanField(default=True, blank=True, null=True)
    SCHEDULE_CHOICES = (
        ('NONE', 'Normal (no schedule)'),
        ('H', 'Schedule H (prescription)'),
        ('H1', 'Schedule H1 (register zaroori)'),
        ('X', 'Schedule X (register zaroori)'),
    )
    schedule = models.CharField(max_length=4, choices=SCHEDULE_CHOICES, default='NONE', verbose_name="Drug Schedule")
    barcode = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name="Barcode")
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                              verbose_name="MRP / Sale rate (per base unit)",
                              help_text="Billing me scan/search karne par rate yahin se bharta hai")

    @property
    def is_low_stock(self):
        return self.stock_qty <= self.reorder_level

    def save(self, *args, **kwargs):
        if self.barcode is not None:
            self.barcode = self.barcode.strip() or None
        if not self.sku:
            new_sku = generate_unique_sku_id()
            while Product.objects.filter(sku=new_sku).exists():
                new_sku = generate_unique_sku_id()
            self.sku = new_sku
        super().save(*args, **kwargs)

    def unit_factor(self, unit):
        """1 'unit' = kitni base unit. Base unit (ya None) ke liye 1."""
        if unit is None or unit.pk == self.unit_id:
            return 1
        conv = self.unit_conversions.filter(unit=unit).first()
        if not conv:
            raise ValidationError(
                f"'{self.name}' me '{unit}' ka conversion set nahi hai. "
                f"Pehle Product page par 'Unit conversions' me add karein."
            )
        return conv.factor

    def stock_display(self):
        """Stock ko pack format me dikhata hai, e.g. '2 Box 3 Strip'."""
        base = self.unit.name if self.unit_id else "unit"
        remaining = self.stock_qty
        parts = []
        # prefetch_related('unit_conversions__unit') ho to extra query nahi lagti
        if 'unit_conversions' in getattr(self, '_prefetched_objects_cache', {}):
            all_convs = self.unit_conversions.all()
        else:
            all_convs = self.unit_conversions.select_related('unit')
        convs = sorted((c for c in all_convs if c.factor > 1), key=lambda c: c.factor, reverse=True)
        for conv in convs:
            qty, remaining = divmod(remaining, conv.factor)
            if qty:
                parts.append(f"{qty} {conv.unit.name}")
        if remaining or not parts:
            parts.append(f"{remaining} {base}")
        return " ".join(parts)

    def get_next_batch(self):
        """FEFO: sabse pehle expire hone wala (non-expired) batch."""
        today = timezone.localdate()
        # Python me filter: prefetch_related('batches') ho to extra query nahi lagti
        live = [b for b in self.batches.all()
                if b.qty > 0 and (b.expire_date is None or b.expire_date >= today)]
        if not live:
            return None
        return min(live, key=lambda b: (b.expire_date is None, b.expire_date or today, b.id))

    def sellable_qty(self):
        """Sirf wo stock jo bik sakta hai (expired batches ko chhod kar), base unit me."""
        today = timezone.localdate()
        return sum(b.qty for b in self.batches.all()
                   if b.qty > 0 and (b.expire_date is None or b.expire_date >= today))

    def expired_qty(self):
        today = timezone.localdate()
        return sum(b.qty for b in self.batches.all()
                   if b.qty > 0 and b.expire_date is not None and b.expire_date < today)

    def get_alternatives(self, limit=2):
        if not self.composition:
            return Product.objects.none()
        return Product.objects.filter(
            composition__iexact=self.composition, stock_qty__gt=0
        ).exclude(pk=self.pk)[:limit]

    def __str__(self):
        return self.name or f"Product #{self.pk}"

    class Meta:
        verbose_name = "05. Product"
        verbose_name_plural = "05. Product"


class ProductUnit(models.Model):
    """Product-wise unit conversion. Example: Dolo 650 -> 1 Strip = 15 Tablet."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='unit_conversions')
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, verbose_name="Bada Unit (Strip/Box)")
    factor = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="1 is unit me kitni base unit hoti hain (e.g. 1 Strip = 15 Tablet to 15)",
    )

    def clean(self):
        if self.product_id and self.unit_id and self.unit_id == self.product.unit_id:
            raise ValidationError({'unit': 'Ye to base unit hi hai, iska conversion nahi chahiye.'})

    def __str__(self):
        base = self.product.unit.name if self.product.unit_id else "base unit"
        return f"1 {self.unit} = {self.factor} {base}"

    class Meta:
        verbose_name = "Unit Conversion"
        verbose_name_plural = "Unit Conversions"
        constraints = [
            models.UniqueConstraint(fields=['product', 'unit'], name='unique_unit_per_product'),
        ]


# ======================================================
# BATCH
# ======================================================

class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches', editable=False, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, editable=False, null=True, blank=True)
    purchase_item = models.OneToOneField('PurchaseItem', on_delete=models.CASCADE, editable=False, null=True, blank=True, related_name='batch_record')
    qty = models.IntegerField(editable=False, default=0)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, editable=False, null=True, blank=True)
    batch_number = models.CharField(max_length=50, editable=False, verbose_name="Batch")
    manufacture_date = models.DateField(editable=False, null=True, blank=True, verbose_name="MFG Date")
    expire_date = models.DateField(editable=False, null=True, blank=True, verbose_name="EXP Date")
    rack = models.CharField(max_length=50, editable=False, blank=True, null=True)
    row = models.CharField(max_length=50, editable=False, blank=True, null=True)
    location = models.CharField(max_length=100, editable=False, blank=True, null=True)

    @property
    def expiry_status(self):
        if not self.expire_date:
            return "No Date"
        today = timezone.localdate()
        if self.expire_date < today:
            return "Expired"
        elif self.expire_date <= today + timedelta(days=30):
            return "Expiring Soon"
        return "Safe"

    def __str__(self):
        return str(self.batch_number) if self.batch_number else "No Batch"

    class Meta:
        verbose_name = "07. Batch"
        verbose_name_plural = "07. Batch"


# ======================================================
# PURCHASE (Invoice header) + PURCHASE ITEMS
# ======================================================

class Purchase(models.Model):
    """Ek supplier invoice: supplier + invoice number + date. Items niche PurchaseItem me."""
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    invoice_number = models.CharField(max_length=50, verbose_name="Invoice No.")
    invoice_date = models.DateField(default=timezone.localdate, verbose_name="Invoice Date")
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.invoice_number:
            self.invoice_number = self.invoice_number.strip()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old = Purchase.objects.filter(pk=self.pk).first() if self.pk else None
            super().save(*args, **kwargs)

            if old:
                # Supplier badla: saare items ka amount purane se naye supplier par shift
                if old.supplier_id != self.supplier_id:
                    total = self.items.aggregate(s=Sum('purchase_price'))['s'] or Decimal('0')
                    adjust_supplier_balance(old.supplier_id, -total)
                    adjust_supplier_balance(self.supplier_id, total)
                    Batch.objects.filter(purchase_item__purchase=self).update(supplier=self.supplier)

                # Date / supplier ka naam ledger me bhi sync
                Transaction.objects.filter(purchase_item__purchase=self).update(
                    purchase_date=self.invoice_date,
                    supplier=self.supplier.company,
                )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for item in self.items.all():
                item.delete()  # stock, batch, balance sab wapas
            super().delete(*args, **kwargs)

    def _sum(self, field):
        # .all() => prefetch_related('items') ho to list page par extra query nahi lagti
        total = sum((getattr(i, field) or Decimal('0') for i in self.items.all()), Decimal('0'))
        return round(total, 2) if total else 0

    @property
    def total_items(self):
        return len(self.items.all())

    @property
    def taxable(self):
        return self._sum('tax')

    @property
    def gst_total(self):
        return self._sum('gst')

    @property
    def total(self):
        return self._sum('purchase_price')

    def __str__(self):
        return f"{self.invoice_number} - {self.supplier.company}"

    class Meta:
        verbose_name = "06. Purchase "
        verbose_name_plural = "06. Purchase "
        constraints = [
            models.UniqueConstraint(fields=['supplier', 'invoice_number'], name='unique_invoice_per_supplier'),
        ]


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.CharField(max_length=255, blank=True, null=True, editable=False)
    manufacture_date = models.DateField(blank=True, null=True, verbose_name="MFG Date")
    expire_date = models.DateField(blank=True, null=True, verbose_name="EXP Date")
    qty = models.IntegerField(verbose_name="Qty")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True,
                             help_text="Khali chhodne par product ki base unit maani jayegi")
    unit_factor = models.PositiveIntegerField(editable=False, default=1)
    base_qty = models.PositiveIntegerField(editable=False, default=0, verbose_name="Qty (base unit)")
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True,
                                         verbose_name="Price (GST ke saath total)")
    rate = models.DecimalField(max_digits=12, decimal_places=4, editable=False, default=0, verbose_name="Rate (per base unit)")
    tax = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0, verbose_name="Taxable Value")
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    gst_rate = models.IntegerField(editable=False, default=0, verbose_name="GST (%)")
    rack = models.CharField(max_length=50, blank=True, null=True, verbose_name="Rack No.")
    row = models.CharField(max_length=50, blank=True, null=True, verbose_name="Row No.")
    location = models.CharField(max_length=100, blank=True, null=True, verbose_name="Location")
    composition = models.CharField(max_length=255, blank=True, null=True, help_text="Example: Paracetamol 500mg")

    def clean(self):
        if self.qty is not None and self.qty <= 0:
            raise ValidationError({'qty': 'Qty 0 se zyada honi chahiye.'})
        if self.purchase_price is None:
            raise ValidationError({'purchase_price': 'Price daalna zaroori hai.'})
        if self.purchase_price < 0:
            raise ValidationError({'purchase_price': 'Price negative nahi ho sakta.'})
        if self.manufacture_date and self.expire_date and self.expire_date < self.manufacture_date:
            raise ValidationError({'expire_date': 'EXP date, MFG date se pehle nahi ho sakti.'})
        if self.product_id and self.unit_id:
            try:
                self.product.unit_factor(self.unit)
            except ValidationError as e:
                raise ValidationError({'unit': e.messages})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old = PurchaseItem.objects.filter(pk=self.pk).first() if self.pk else None
            header = self.purchase
            product = self.product

            # ---- Unit conversion: entered qty -> base qty ----
            if self.unit_id is None:
                self.unit = product.unit
            self.unit_factor = resolve_unit_factor(self, old)
            qty = self.qty or 0
            self.base_qty = qty * self.unit_factor

            # ---- Guards: maal bik/return ho chuka ho to edit se stock na bigde ----
            if old:
                if old.return_items.exists():
                    raise ValidationError(
                        'Is purchase item par Purchase Return ban chuka hai, isliye edit nahi ho sakta.')
                old_batch = Batch.objects.filter(purchase_item=old).first()
                consumed = max((old.base_qty or 0) - old_batch.qty, 0) if old_batch else 0
                if consumed and old.product_id != self.product_id:
                    raise ValidationError(
                        f"Is item ka {consumed} maal bik/kharch ho chuka hai, product badla nahi ja sakta.")
                if consumed and self.base_qty < consumed:
                    raise ValidationError(
                        f"Is item ka {consumed} maal pehle hi bik/kharch ho chuka hai, "
                        f"qty {consumed} (base unit) se kam nahi ho sakti.")

            # ---- GST calculation (Decimal) ----
            price = Decimal(self.purchase_price or 0)
            if old and old.product_id == self.product_id:
                self.gst_rate = old.gst_rate   # purani entry par us waqt ka GST rate hi rahe
            else:
                self.gst_rate = int(product.gst_rate)
            gst_rate = Decimal(self.gst_rate)

            if self.base_qty > 0:
                self.tax = (price * 100 / (gst_rate + 100)).quantize(TWO)
                self.rate = (self.tax / self.base_qty).quantize(RATE_Q)
                self.gst = (price - self.tax).quantize(TWO)
                self.cgst = (self.gst / 2).quantize(TWO)
                self.sgst = self.gst - self.cgst
            else:
                self.tax = self.rate = self.gst = self.cgst = self.sgst = Decimal('0.00')

            if not self.batch:
                self.batch = generate_unique_batch_number()

            super().save(*args, **kwargs)

            # ---- Product stock sync (base unit me) ----
            old_same_product = bool(old and old.product_id == self.product_id)
            if old and not old_same_product:
                old_product = Product.objects.get(pk=old.product_id)
                old_product.stock_qty = max(0, old_product.stock_qty - (old.base_qty or 0))
                old_product.save(update_fields=['stock_qty'])

            prev_base = (old.base_qty or 0) if old_same_product else 0
            fresh = Product.objects.get(pk=self.product_id)
            fresh.stock_qty = max(0, fresh.stock_qty + self.base_qty - prev_base)
            fresh.batch = self.batch
            if self.rack:
                fresh.rack = self.rack
            if self.row:
                fresh.row = self.row
            if self.location:
                fresh.location = self.location
            if self.composition:
                fresh.composition = self.composition
            fresh.save()

            # ---- Batch sync (create + edit), qty base unit me ----
            batch_obj, created = Batch.objects.get_or_create(
                purchase_item=self,
                defaults=dict(
                    product=product, supplier=header.supplier, qty=self.base_qty,
                    unit=product.unit, batch_number=self.batch,
                    manufacture_date=self.manufacture_date, expire_date=self.expire_date,
                    rack=self.rack, row=self.row, location=self.location,
                ),
            )
            if not created:
                prev_base_b = (old.base_qty or 0) if old else self.base_qty
                batch_obj.qty = max(0, batch_obj.qty + self.base_qty - prev_base_b)
                batch_obj.product = product
                batch_obj.supplier = header.supplier
                batch_obj.unit = product.unit
                batch_obj.manufacture_date = self.manufacture_date
                batch_obj.expire_date = self.expire_date
                batch_obj.rack = self.rack
                batch_obj.row = self.row
                batch_obj.location = self.location
                batch_obj.save()

            # ---- Transaction (ledger) sync ----
            Transaction.objects.update_or_create(
                purchase_item=self,
                defaults=dict(
                    purchase_date=header.invoice_date,
                    supplier=header.supplier.company,
                    purchase_amount=Decimal(self.purchase_price or 0),
                ),
            )

            # ---- Supplier balance sync ----
            new_price = Decimal(self.purchase_price or 0)
            old_price = Decimal(old.purchase_price or 0) if old else Decimal('0')
            adjust_supplier_balance(header.supplier_id, new_price - old_price)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            batch = Batch.objects.filter(purchase_item=self).first()
            if batch and batch.qty < (self.base_qty or 0):
                raise ValidationError(
                    f"Is purchase ka {(self.base_qty or 0) - batch.qty} (base unit) maal bik/kharch/return ho chuka hai, "
                    f"isliye ise delete nahi kar sakte. Galti ho to Purchase Return banayein.")
            product = Product.objects.get(pk=self.product_id)
            product.stock_qty = max(0, product.stock_qty - (self.base_qty or 0))
            product.save(update_fields=['stock_qty'])

            adjust_supplier_balance(self.purchase.supplier_id, -Decimal(self.purchase_price or 0))
            # Batch aur Transaction CASCADE se khud delete ho jayenge
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} x {self.qty} {self.unit or ''}".strip()

    class Meta:
        verbose_name = "Purchase Item"
        verbose_name_plural = "Purchase Items"


# ======================================================
# SALE
# ======================================================

class Sale(models.Model):
    # Bill ki date se set hoti hai (pehle auto_now_add tha => backdated/online bill galat mahine me jaate the)
    date = models.DateField(default=timezone.localdate, null=True)
    name = models.CharField(max_length=255, editable=False, null=True)
    invoice_mode = models.CharField(max_length=10, editable=False)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    invoice_item = models.OneToOneField('InvoiceItem', on_delete=models.CASCADE, null=True, blank=True)
    # Sales return par negative Sale row banti hai (dashboard/P&L/ledger khud net ho jaate hain)
    sales_return_item = models.OneToOneField('SalesReturnItem', on_delete=models.CASCADE, null=True, blank=True,
                                             editable=False, related_name='sale_entry')

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            Transaction.objects.update_or_create(
                sale=self,
                defaults=dict(
                    sale_date=self.date,
                    customer=self.name,
                    sale_amount=self.total,
                ),
            )

    def __str__(self):
        return str(self.name) if self.name else f"Sale #{self.id}"

    class Meta:
        verbose_name = "10. Sales"
        verbose_name_plural = "10. Sales"


# ======================================================
# CUSTOMER ORDER
# ======================================================

class Order(models.Model):
    order_id = models.CharField(max_length=20, unique=True, null=True, editable=False, blank=True)
    customers_name = models.CharField(max_length=100, blank=True, null=True)
    customer_product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True)
    customer_rate = models.IntegerField(blank=True, null=True)
    customer_qty = models.IntegerField(blank=True, null=True)
    customer_unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Unit")
    order_date = models.DateField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.order_id:
            new_id = generate_unique_order_id()
            while Order.objects.filter(order_id=new_id).exists():
                new_id = generate_unique_order_id()
            self.order_id = new_id
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "08. Order "
        verbose_name_plural = "08. Orders"

    def __str__(self):
        return f"{self.order_id} "


# ======================================================
# SHOP PROFILE + INVOICE NUMBERING
# ======================================================

class ShopProfile(models.Model):
    """Dukaan ki details (invoice par chhapti hain). Sirf ek record rehta hai."""
    name = models.CharField(max_length=255, default="My Pharmacy", verbose_name="Shop Name")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField(max_length=15, blank=True, verbose_name="GSTIN")
    drug_license = models.CharField(max_length=255, blank=True, verbose_name="Drug Licence No.",
                                    help_text="Jaise: DL No. 20/21/12345, 21/21/12345")
    terms = models.TextField(
        blank=True,
        default="Goods once sold will not be taken back or exchanged.\nPlease check batch and expiry at the time of purchase.",
        verbose_name="Terms & Conditions",
    )

    @classmethod
    def get(cls):
        return cls.objects.first() or cls.objects.create()

    @property
    def state(self):
        return GST_STATE_CODES.get(self.gstin[:2], "") if self.gstin else ""

    def save(self, *args, **kwargs):
        self.gstin = (self.gstin or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Shop Profile"
        verbose_name_plural = "Shop Profile"


class InvoiceCounter(models.Model):
    """Har financial year (Apr-Mar) ka alag, bina gap wala invoice counter."""
    fiscal_year = models.CharField(max_length=7, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.fiscal_year}: {self.last_number}"


def fiscal_year(d=None):
    d = d or timezone.localdate()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_invoice_number(on=None):
    """
    INV/2026-27/0001 jaisa agla number. Bill ke saath hi ek transaction me chalta hai,
    isliye bill fail/rollback ho to number kharch nahi hota (koi gap nahi).
    UPDATE ... SET n = n + 1 database level par lock leta hai, to do log ek saath bill
    banayein tab bhi duplicate number nahi banta.
    `on` = bill ki date: financial year usi date se nikalta hai (aaj ki date se nahi).
    """
    fy = fiscal_year(on)
    with transaction.atomic():
        counter, _ = InvoiceCounter.objects.get_or_create(fiscal_year=fy)
        InvoiceCounter.objects.filter(pk=counter.pk).update(last_number=F('last_number') + 1)
        counter.refresh_from_db()
    return f"INV/{fy}/{counter.last_number:04d}"


# ======================================================
# BILL
# ======================================================

class Bill(models.Model):
    MODE_CHOICES = (
        ('online', 'Online'),
        ('offline', 'Offline'),
    )
    STATUS_CHOICES = (
        ('FINAL', 'Final'),
        ('CANCELLED', 'Cancelled'),
    )
    invoice_number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='FINAL', editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    PAYMENT_CHOICES = (
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('CREDIT', 'Udhar (Credit)'),
    )
    customer_name = models.CharField(max_length=255, default="Cash")
    customer = models.ForeignKey('Customer', on_delete=models.PROTECT, null=True, blank=True, related_name='bills',
                                 help_text="Regular customer / udhar ke liye select karein")
    date = models.DateField(blank=True, null=True)
    invoice_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='offline')
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True)
    payment_mode = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default='CASH')
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                      verbose_name="Abhi diya (sirf udhar bill me)",
                                      help_text="Udhar bill me customer ne abhi kitna diya (baaki udhar me jayega)")
    doctor_name = models.CharField(max_length=100, blank=True, default='')
    doctor_reg_no = models.CharField(max_length=50, blank=True, default='', verbose_name="Doctor Reg. No.")
    patient_name = models.CharField(max_length=100, blank=True, default='')
    patient_address = models.CharField(max_length=255, blank=True, default='')

    def clean(self):
        if self.invoice_mode == 'online':
            if not self.order_id:
                raise ValidationError({'order': 'Online invoice ke liye Order select karna zaroori hai.'})
            o = self.order
            if not o.customer_product_id or not o.customer_qty or o.customer_rate is None:
                raise ValidationError({'order': 'Is Order me product / rate / qty missing hai.'})
        else:
            self.order = None
        if self.payment_mode == 'CREDIT':
            if not self.customer_id:
                raise ValidationError({'customer': 'Udhar bill ke liye Customer select karna zaroori hai.'})
            if self.amount_paid is not None and self.amount_paid < 0:
                raise ValidationError({'amount_paid': 'Amount negative nahi ho sakta.'})
        else:
            self.amount_paid = Decimal('0.00')   # udhar nahi hai to poora paisa mil chuka maana jata hai

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.customer_id and (not self.customer_name or self.customer_name == "Cash"):
                self.customer_name = self.customer.name
            if self.invoice_mode == 'online' and self.order:
                if self.customer_name == "Cash" and self.order.customers_name:
                    self.customer_name = self.order.customers_name
                if not self.date and self.order.order_date:
                    self.date = self.order.order_date
            if not self.date:
                self.date = timezone.localdate()

            # Naya bill: gap-free invoice number
            if self.pk is None and not self.invoice_number:
                self.invoice_number = next_invoice_number(self.date)

            super().save(*args, **kwargs)

            # Online bill par item auto-create (duplicate se bachne ke liye check)
            if self.invoice_mode == 'online' and self.order and not self.items.exists():
                InvoiceItem.objects.create(
                    bill=self,
                    product=self.order.customer_product,
                    rate=self.order.customer_rate,
                    qty=self.order.customer_qty,
                    unit=self.order.customer_unit,
                )

    def after_items_saved(self):
        """Items save hone ke baad ke checks. ValidationError aaye to poora bill rollback ho jata hai."""
        if self.payment_mode != 'CREDIT' or not self.customer_id:
            return
        limit = self.customer.credit_limit or Decimal('0')
        if limit > 0:
            balance = self.customer.balance()
            if balance > limit:
                raise ValidationError(
                    f"Customer ka udhar limit ₹{limit} hai, is bill ke baad baaki ₹{balance} ho jayega.")

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for item in self.items.all():
                item.delete()  # stock + batch wapas
            super().delete(*args, **kwargs)

    def cancel(self):
        """Bill cancel: stock/batch wapas, Sales se hatao. Bill aur items record ke liye bache rehte hain."""
        if self.status == 'CANCELLED':
            return
        if self.returns.exists():
            raise ValidationError('Is bill par Credit Note (Sales Return) ban chuka hai, isliye bill cancel nahi ho sakta. '
                                  'Pehle return delete karein.')
        with transaction.atomic():
            for item in self.items.all():
                if item.product_id and item.base_qty:
                    product = Product.objects.select_for_update().get(pk=item.product_id)
                    product.stock_qty += item.base_qty
                    product.save(update_fields=['stock_qty'])
                    item._restore_to_exact_batches()
                    InvoiceItem.objects.filter(pk=item.pk).update(batch_history={})
                Sale.objects.filter(invoice_item=item).delete()  # Transaction bhi cascade se hat jata hai
            self.status = 'CANCELLED'
            self.cancelled_at = timezone.now()
            super().save(update_fields=['status', 'cancelled_at'])

    @property
    def grand_total(self):
        """Rupaye me round kiya hua total (invoice par jo chhapta hai)."""
        return Decimal(self.total or 0).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def credit_due(self):
        """Is udhar bill ka bill-time par baaki hissa (receipts alag se Customer balance me ghatate hain)."""
        if self.status != 'FINAL' or self.payment_mode != 'CREDIT':
            return Decimal('0')
        return max(self.grand_total - (self.amount_paid or Decimal('0')), Decimal('0'))

    @property
    def display_number(self):
        return self.invoice_number or f"BILL-{self.id}"

    def __str__(self):
        return f"{self.display_number} - {self.customer_name}"

    def _sum(self, field):
        # .all() => prefetch_related('items') ho to list page par extra query nahi lagti
        total = sum((getattr(i, field) or Decimal('0') for i in self.items.all()), Decimal('0'))
        return round(total, 2) if total else 0

    @property
    def total(self):
        return self._sum('total')

    @property
    def total_items(self):
        return len(self.items.all())

    @property
    def taxable(self):
        return self._sum('taxable_value')

    @property
    def cgst(self):
        return self._sum('cgst')

    @property
    def sgst(self):
        return self._sum('sgst')

    @property
    def igst(self):
        return self._sum('igst')

    class Meta:
        verbose_name = "09. Bills"
        verbose_name_plural = "09. Bills"


# ======================================================
# INVOICE ITEM
# ======================================================

class InvoiceItem(models.Model):
    bill = models.ForeignKey(Bill, related_name='items', on_delete=models.CASCADE, blank=True, null=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True)
    batch_history = models.JSONField(default=dict, blank=True, null=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Rate chuni hui unit ke hisaab se")
    qty = models.IntegerField(blank=True, null=True)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True,
                             help_text="Khali chhodne par product ki base unit maani jayegi")
    unit_factor = models.PositiveIntegerField(editable=False, default=1)
    base_qty = models.PositiveIntegerField(editable=False, default=0, verbose_name="Qty (base unit)")
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True, verbose_name="Discount (%)")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Discount (Flat)")
    gst_rate = models.IntegerField(default=0, editable=False)
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    igst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    is_igst = models.BooleanField(default=False, verbose_name="Apply IGST")
    total = models.DecimalField(max_digits=10, decimal_places=2, editable=False, blank=True, null=True)

    # ---------- Validation (admin form me error dikhane ke liye) ----------
    def clean(self):
        if not self.product_id or not self.qty:
            return
        if self.qty <= 0:
            raise ValidationError({'qty': 'Qty 0 se zyada honi chahiye.'})

        old = InvoiceItem.objects.filter(pk=self.pk).first() if self.pk else None

        try:
            factor = resolve_unit_factor(self, old)
        except ValidationError as e:
            raise ValidationError({'unit': e.messages})

        old_base = 0
        if old and old.product_id == self.product_id:
            old_base = old.base_qty or 0

        needed = self.qty * factor
        # Sirf non-expired batches ka stock bik sakta hai (FEFO expired batch skip karta hai)
        available = self.product.sellable_qty() + old_base
        if needed > available:
            base_name = self.product.unit.name if self.product.unit_id else 'unit'
            expired = self.product.expired_qty()
            extra = f' (Expired stock {expired} {base_name} alag hai, wo bik nahi sakta)' if expired else ''
            raise ValidationError({
                'qty': f'Stock kam hai. Chahiye: {needed} {base_name}, Bikne layak: {available} {base_name}{extra}'
            })

        if self.discount_percent and self.discount_amount:
            raise ValidationError('Discount % ya Flat me se sirf ek hi use karein.')
        if self.discount_percent and self.discount_percent > 100:
            raise ValidationError({'discount_percent': 'Discount 100% se zyada nahi ho sakta.'})

    # ---------- Save ----------
    def save(self, *args, **kwargs):
        bill = self.bill
        if bill and bill.invoice_mode == 'online' and bill.order:
            order = bill.order
            self.product = order.customer_product
            self.rate = order.customer_rate
            self.qty = order.customer_qty
            self.unit = order.customer_unit

        if not self.product_id or not self.qty or self.qty <= 0:
            raise ValidationError("Product aur Qty (0 se zyada) zaroori hai.")

        if self.unit_id is None:
            self.unit = self.product.unit

        with transaction.atomic():
            old = InvoiceItem.objects.filter(pk=self.pk).first() if self.pk else None
            if old and old.return_items.exists():
                raise ValidationError('Is bill item par Return/Credit Note ban chuka hai, isliye edit nahi ho sakta.')

            # ---- Unit conversion ----
            self.unit_factor = resolve_unit_factor(self, old)
            self.base_qty = self.qty * self.unit_factor

            # ---- Calculation (rate chuni hui unit ke hisaab se) ----
            if old and old.product_id == self.product_id:
                self.gst_rate = old.gst_rate   # purane bill par us waqt ka GST rate hi rahe
            else:
                self.gst_rate = int(self.product.gst_rate)
            rate_val = Decimal(str(self.rate or 0))
            gross = rate_val * Decimal(self.qty)
            pct = Decimal(str(self.discount_percent or 0))
            flat = Decimal(str(self.discount_amount or 0))
            discount = gross * (pct / Decimal('100')) + flat

            taxable = max(gross - discount, Decimal('0')).quantize(TWO)
            gst = (taxable * Decimal(self.gst_rate) / Decimal('100')).quantize(TWO)

            self.taxable_value = taxable
            self.gst = gst
            if self.is_igst:
                self.cgst = Decimal('0')
                self.sgst = Decimal('0')
                self.igst = gst
            else:
                self.igst = Decimal('0')
                self.cgst = (gst / 2).quantize(TWO)
                self.sgst = gst - self.cgst
            self.total = (taxable + gst).quantize(TWO)

            # ---- Stock + Batch (hamesha base unit me) ----
            product = Product.objects.select_for_update().get(pk=self.product_id)

            if old is None:
                self.batch_history = {}
                self._take_stock(product, self.base_qty)

            elif old.product_id != self.product_id:
                # Product badal gaya: purana pura wapas, naya pura minus
                if old.product_id:
                    old._restore_to_exact_batches()
                    old_product = Product.objects.get(pk=old.product_id)
                    old_product.stock_qty += (old.base_qty or 0)
                    old_product.save(update_fields=['stock_qty'])
                self.batch_history = {}
                self._take_stock(product, self.base_qty)

            else:
                self.batch_history = dict(old.batch_history or {})
                diff = self.base_qty - (old.base_qty or 0)
                if diff > 0:
                    self._take_stock(product, diff)
                elif diff < 0:
                    self._return_stock(product, abs(diff))

            super().save(*args, **kwargs)

            Sale.objects.update_or_create(
                invoice_item=self,
                defaults=dict(
                    date=(self.bill.date if self.bill and self.bill.date else timezone.localdate()),
                    name=self.bill.customer_name if self.bill else "Cash",
                    invoice_mode=self.bill.invoice_mode if self.bill else 'offline',
                    taxable_value=self.taxable_value,
                    gst=self.gst,
                    total=self.total,
                ),
            )

            self._auto_create_purchase_order(product)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.product_id and self.base_qty:
                product = Product.objects.select_for_update().get(pk=self.product_id)
                product.stock_qty += self.base_qty
                product.save(update_fields=['stock_qty'])
                self._restore_to_exact_batches()
            # Sale aur Transaction CASCADE se delete ho jayenge
            super().delete(*args, **kwargs)

    # ---------- Stock helpers (qty = base unit) ----------
    def _take_stock(self, product, base_qty):
        if product.stock_qty < base_qty:
            raise ValidationError(f"'{product}' ka stock kam hai. Available: {product.stock_qty}")
        product.stock_qty -= base_qty
        product.save(update_fields=['stock_qty'])
        self._deduct_from_batches(base_qty)

    def _return_stock(self, product, base_qty):
        product.stock_qty += base_qty
        product.save(update_fields=['stock_qty'])
        self._add_to_batches(base_qty)

    # ---------- FEFO ----------
    def _deduct_from_batches(self, required_qty):
        """FEFO: jo pehle expire hoga wo pehle bikega. Expired batch skip hota hai."""
        today = timezone.localdate()
        batches = (
            Batch.objects.select_for_update()
            .filter(product_id=self.product_id, qty__gt=0)
            .filter(Q(expire_date__isnull=True) | Q(expire_date__gte=today))
            .order_by(F('expire_date').asc(nulls_last=True), 'id')
        )

        remaining = required_qty
        history = dict(self.batch_history or {})

        for batch in batches:
            if remaining <= 0:
                break
            take = min(batch.qty, remaining)
            batch.qty -= take
            batch.save(update_fields=['qty'])
            key = str(batch.id)
            history[key] = history.get(key, 0) + take
            remaining -= take

        if remaining > 0:
            raise ValidationError(
                f"Valid (non-expired) batches me itni quantity available nahi hai. Short by: {remaining}"
            )
        self.batch_history = history

    def _add_to_batches(self, qty):
        """Qty kam hone par: jis batch se last me nikala tha, usi me wapas daalta hai."""
        history = dict(self.batch_history or {})
        remaining = qty
        for key in reversed(list(history.keys())):
            if remaining <= 0:
                break
            used = history[key]
            give = min(used, remaining)
            batch = Batch.objects.filter(pk=int(key)).first()
            if batch:
                batch.qty += give
                batch.save(update_fields=['qty'])
            history[key] = used - give
            if history[key] <= 0:
                del history[key]
            remaining -= give
        self.batch_history = history

    def _restore_to_exact_batches(self):
        """Delete par poori qty wapas usi batch me."""
        history = self.batch_history or {}
        if history:
            for key, qty in history.items():
                batch = Batch.objects.filter(pk=int(key)).first()
                if batch:
                    batch.qty += qty
                    batch.save(update_fields=['qty'])
            self.batch_history = {}
        elif self.product_id and self.base_qty:
            # Purane invoice jinme history nahi thi (fallback)
            batch = (
                Batch.objects.filter(product_id=self.product_id)
                .order_by(F('expire_date').desc(nulls_last=True))
                .first()
            )
            if batch:
                batch.qty += self.base_qty
                batch.save(update_fields=['qty'])

    # ---------- Auto Purchase Order (qty base unit me) ----------
    def _auto_create_purchase_order(self, product):
        if product.stock_qty > product.reorder_level:
            return

        supplier = product.default_supplier
        if not supplier:
            last = (
                PurchaseItem.objects.filter(product=product)
                .select_related('purchase__supplier').order_by('-id').first()
            )
            supplier = last.purchase.supplier if last else None
        if not supplier:
            return

        # PENDING ya SENT PO already hai to naya mat banao
        if PurchaseOrderItem.objects.filter(
            product=product, purchase_order__status__in=['PENDING', 'SENT']
        ).exists():
            return

        po = (
            PurchaseOrder.objects.filter(supplier=supplier, status='PENDING').first()
            or PurchaseOrder.objects.create(supplier=supplier)
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            product=product,
            order_qty=max(10, product.reorder_level * 2),
        )

    def __str__(self):
        return f"{self.product} x {self.qty} {self.unit or ''}".strip()


# ======================================================
# PAYMENT
# ======================================================

class Payment(models.Model):
    TRANSACTION_TYPES = (
        ('CASH', 'Cash'),
        ('BANK', 'Bank'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due = models.DecimalField(max_digits=12, decimal_places=2, editable=False, blank=True, null=True)
    payment_mode = models.CharField(max_length=50, choices=TRANSACTION_TYPES, blank=True, null=True)
    payment_date = models.DateField(auto_now_add=True)

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': 'Amount 0 se zyada hona chahiye.'})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old = Payment.objects.filter(pk=self.pk).first() if self.pk else None

            if old and old.supplier_id != self.supplier_id:
                adjust_supplier_balance(old.supplier_id, old.amount)   # purane supplier ko wapas
                adjust_supplier_balance(self.supplier_id, -self.amount)
            else:
                diff = self.amount - (old.amount if old else 0)
                adjust_supplier_balance(self.supplier_id, -diff)

            self.due = Supplier.objects.get(pk=self.supplier_id).opening_balance
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            adjust_supplier_balance(self.supplier_id, self.amount)
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"Payment #{self.pk} - {self.supplier}"

    class Meta:
        verbose_name = "11. Payment"
        verbose_name_plural = "11. Payment"


# ======================================================
# TRANSACTION (auto ledger)
# ======================================================

class Transaction(models.Model):
    purchase_item = models.OneToOneField(PurchaseItem, on_delete=models.CASCADE, null=True, blank=True, editable=False)
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, null=True, blank=True, editable=False)
    purchase_return_item = models.OneToOneField('PurchaseReturnItem', on_delete=models.CASCADE, null=True,
                                                blank=True, editable=False)
    purchase_date = models.DateField(editable=False, blank=True, null=True)
    supplier = models.CharField(max_length=255, editable=False, blank=True, null=True)
    purchase_amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, blank=True, null=True)
    sale_date = models.DateField(editable=False, blank=True, null=True)
    customer = models.CharField(max_length=255, editable=False, blank=True, null=True)
    sale_amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, blank=True, null=True)

    class Meta:
        verbose_name = "13. Transaction"
        verbose_name_plural = "13. Transaction"


# ======================================================
# EXPENSE + REPORT
# ======================================================

class Expense(models.Model):
    EXPENSES_TYPES = (
        ('RENT', 'Rent'),
        ('SALARY', 'Salary'),
        ('ELECTRICITY', 'Electricity'),
        ('WATER', 'Water'),
        ('MAINTENANCE', 'Maintenance'),
        ('OTHER', 'Other'),
    )
    date = models.DateField(default=timezone.localdate)
    name = models.CharField(max_length=255, help_text="e.g., Rent, Salary, Electricity", choices=EXPENSES_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': 'Amount 0 se zyada hona chahiye.'})

    def __str__(self):
        return f"{self.name} - ₹{self.amount}"

    class Meta:
        verbose_name = "14. Expense"
        verbose_name_plural = "14. Expenses"


class FinancialReport(models.Model):
    class Meta:
        managed = False
        verbose_name = "15. Profit & Loss Report"
        verbose_name_plural = "15. Profit & Loss Reports"


# ======================================================
# PURCHASE ORDER
# ======================================================

class PurchaseOrder(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending (Not Sent)'),
        ('SENT', 'Sent to Supplier'),
        ('COMPLETED', 'Received/Completed'),
        ('CANCELLED', 'Cancelled'),
    )
    po_number = models.CharField(max_length=20, unique=True, default=generate_po_number, editable=False)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    date_created = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    def save(self, *args, **kwargs):
        if self._state.adding:
            while PurchaseOrder.objects.filter(po_number=self.po_number).exists():
                self.po_number = generate_po_number()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.po_number} - {self.supplier.company}"

    class Meta:
        verbose_name = "16. Purchase Order"
        verbose_name_plural = "16. Purchase Orders"


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    order_qty = models.PositiveIntegerField(default=10, help_text="Base unit me quantity")

    def __str__(self):
        return f"{self.product.name} (Qty: {self.order_qty})"


# Naye models (customer, returns, write-off, audit) alag file me; yahin se register hote hain
from .models_extra import *  # noqa: E402,F401,F403
