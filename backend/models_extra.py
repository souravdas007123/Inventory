"""
Naye models: Customer + udhar receipts, Sales/Purchase Return (credit/debit note),
Stock write-off, Physical stock audit.

Ye `backend/models.py` ke end se import hote hain (isliye Django inhe isi app ke models maanta hai).
Saare stock/ledger effects model ke save()/delete() me hain, taaki UI aur admin dono me same rahe.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import (
    TWO, Batch, InvoiceItem, Product, PurchaseItem, Sale, Transaction,
    adjust_supplier_balance, fiscal_year,
)


# ======================================================
# DOCUMENT NUMBERS (Credit Note / Debit Note)
# ======================================================

class DocCounter(models.Model):
    """Har series (CN, DN) ka financial-year wise gap-free counter."""
    series = models.CharField(max_length=10)
    fiscal_year = models.CharField(max_length=7)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['series', 'fiscal_year'], name='unique_series_per_fy'),
        ]

    def __str__(self):
        return f"{self.series} {self.fiscal_year}: {self.last_number}"


def next_doc_number(series, on=None):
    """CN/2026-27/0001 jaisa agla number (invoice counter jaisa hi tareeka)."""
    fy = fiscal_year(on)
    with transaction.atomic():
        counter, _ = DocCounter.objects.get_or_create(series=series, fiscal_year=fy)
        DocCounter.objects.filter(pk=counter.pk).update(last_number=F('last_number') + 1)
        counter.refresh_from_db()
    return f"{series}/{fy}/{counter.last_number:04d}"


# ======================================================
# CUSTOMER + UDHAR LEDGER
# ======================================================

class Customer(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gstin = models.CharField(max_length=15, blank=True, null=True, verbose_name="GSTIN",
                             help_text="B2B bill ke liye (optional)")
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                       help_text="Udhar ki limit. 0 = koi limit nahi")
    is_active = models.BooleanField(default=True)
    created_at = models.DateField(auto_now_add=True, null=True)

    def clean(self):
        if self.gstin:
            self.gstin = self.gstin.strip().upper()
            if len(self.gstin) != 15:
                raise ValidationError({'gstin': 'GSTIN 15 characters ka hona chahiye.'})
        if self.credit_limit is not None and self.credit_limit < 0:
            raise ValidationError({'credit_limit': 'Limit negative nahi ho sakti.'})

    def save(self, *args, **kwargs):
        self.gstin = (self.gstin or '').strip().upper() or None
        super().save(*args, **kwargs)

    def balance(self, exclude_payment=None):
        """
        Customer se kitna lena baaki hai:
        (udhar bills ka baaki hissa) - (receipts) - (udhar me adjust hue returns).
        Ye hamesha calculate hota hai (store nahi), isliye kabhi out-of-sync nahi hota.
        """
        credit_bills = self.bills.filter(status='FINAL', payment_mode='CREDIT').prefetch_related('items')
        due = sum((b.credit_due for b in credit_bills), Decimal('0'))
        receipts = self.payments.all()
        if exclude_payment:
            receipts = receipts.exclude(pk=exclude_payment)
        paid = receipts.aggregate(s=Sum('amount'))['s'] or Decimal('0')
        adjusted = SalesReturnItem.objects.filter(
            sales_return__bill__customer=self, sales_return__refund_mode='ADJUST'
        ).aggregate(s=Sum('total'))['s'] or Decimal('0')
        return due - paid - adjusted

    def __str__(self):
        return self.name + (f" ({self.phone})" if self.phone else "")

    class Meta:
        verbose_name = "Customer"
        verbose_name_plural = "Customers"


class CustomerPayment(models.Model):
    """Customer se udhar ka paisa wapas mila (receipt)."""
    MODES = (
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Card'),
        ('BANK', 'Bank'),
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    mode = models.CharField(max_length=10, choices=MODES, default='CASH')
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True, default='')

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': 'Amount 0 se zyada hona chahiye.'})
        if self.customer_id and self.amount:
            balance = self.customer.balance(exclude_payment=self.pk)
            if self.amount > balance:
                raise ValidationError({'amount': f'Amount baaki udhar (₹{balance}) se zyada hai.'})

    def __str__(self):
        return f"Receipt #{self.pk} - {self.customer} ₹{self.amount}"

    class Meta:
        verbose_name = "Customer Payment"
        verbose_name_plural = "Customer Payments"


# ======================================================
# SALES RETURN (Credit Note)
# ======================================================

class SalesReturn(models.Model):
    REFUND_CHOICES = (
        ('CASH', 'Cash / UPI wapas diya'),
        ('ADJUST', 'Customer ke udhar me adjust'),
    )
    note_number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    bill = models.ForeignKey('Bill', on_delete=models.PROTECT, related_name='returns')
    date = models.DateField(default=timezone.localdate)
    refund_mode = models.CharField(max_length=10, choices=REFUND_CHOICES, default='CASH')
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.bill_id:
            if self.bill.status != 'FINAL':
                raise ValidationError({'bill': 'Cancelled bill par return nahi ban sakta.'})
            if self.refund_mode == 'ADJUST' and not self.bill.customer_id:
                raise ValidationError({'refund_mode': 'Udhar me adjust ke liye bill par Customer hona zaroori hai.'})

    def save(self, *args, **kwargs):
        if not self.note_number:
            self.note_number = next_doc_number('CN', self.date)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for item in self.items.all():
                item.delete()   # stock/batch/sale wapas ulta
            super().delete(*args, **kwargs)

    def _sum(self, field):
        return sum((getattr(i, field) or Decimal('0') for i in self.items.all()), Decimal('0'))

    @property
    def total(self):
        return self._sum('total')

    @property
    def taxable(self):
        return self._sum('taxable_value')

    @property
    def total_items(self):
        return len(self.items.all())

    def __str__(self):
        return self.note_number or f"Return #{self.pk}"

    class Meta:
        verbose_name = "Sales Return (Credit Note)"
        verbose_name_plural = "Sales Returns (Credit Notes)"


class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name='items')
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.PROTECT, related_name='return_items')
    qty = models.IntegerField(verbose_name="Return Qty", help_text="Bill me jis unit me becha tha, usi me")
    base_qty = models.PositiveIntegerField(editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    igst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    batch_returns = models.JSONField(default=dict, editable=False, blank=True)   # {batch_id: base qty}
    cost = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)   # wapas aaye maal ki purchase cost

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Return item edit nahi hota. Galti ho to return delete karke naya banayein.')
        if not self.qty or self.qty <= 0:
            raise ValidationError('Return qty 0 se zyada honi chahiye.')

        with transaction.atomic():
            item = InvoiceItem.objects.select_for_update().select_related('product', 'unit').get(pk=self.invoice_item_id)
            bill = self.sales_return.bill
            if item.bill_id != bill.pk:
                raise ValidationError('Ye item is bill ka nahi hai.')
            if not item.product_id or not item.qty:
                raise ValidationError('Is bill item me product/qty nahi hai.')

            previous = list(SalesReturnItem.objects.filter(invoice_item=item))
            returned_qty = sum(p.qty for p in previous)
            if self.qty + returned_qty > item.qty:
                raise ValidationError(
                    f"'{item.product}' me sirf {item.qty - returned_qty} {item.unit or ''} wapas ho sakta hai.")

            # ---- Amounts (aakhri hissa = baaki sab, taaki rounding ka paisa na bache) ----
            if self.qty + returned_qty == item.qty:
                taxable = item.taxable_value - sum((p.taxable_value for p in previous), Decimal('0'))
                gst = item.gst - sum((p.gst for p in previous), Decimal('0'))
            else:
                ratio = Decimal(self.qty) / Decimal(item.qty)
                taxable = (item.taxable_value * ratio).quantize(TWO)
                gst = (item.gst * ratio).quantize(TWO)
            self.taxable_value = taxable
            self.gst = gst
            if item.is_igst:
                self.igst, self.cgst, self.sgst = gst, Decimal('0'), Decimal('0')
            else:
                self.igst = Decimal('0')
                self.cgst = (gst / 2).quantize(TWO)
                self.sgst = gst - self.cgst
            self.total = taxable + gst
            self.base_qty = self.qty * (item.unit_factor or 1)

            # ---- Stock + batch wapas (jis batch se nikla tha usi me, last se pehle) ----
            product = Product.objects.select_for_update().get(pk=item.product_id)
            product.stock_qty += self.base_qty
            product.save(update_fields=['stock_qty'])

            already = {}
            for p in previous:
                for key, q in (p.batch_returns or {}).items():
                    already[key] = already.get(key, 0) + q

            remaining = self.base_qty
            restored = {}
            history = item.batch_history or {}
            for key in reversed(list(history.keys())):
                if remaining <= 0:
                    break
                give = min(history[key] - already.get(key, 0), remaining)
                if give <= 0:
                    continue
                batch = Batch.objects.select_for_update().filter(pk=int(key)).first()
                if batch is None:
                    continue
                batch.qty += give
                batch.save(update_fields=['qty'])
                restored[key] = give
                remaining -= give
            if remaining > 0:   # purane bill jinme batch history nahi thi
                fallback = (Batch.objects.select_for_update().filter(product_id=item.product_id)
                            .order_by(F('expire_date').desc(nulls_last=True), '-id').first())
                if fallback:
                    fallback.qty += remaining
                    fallback.save(update_fields=['qty'])
                    restored[str(fallback.pk)] = restored.get(str(fallback.pk), 0) + remaining
            self.batch_returns = restored

            rates = dict(Batch.objects.filter(pk__in=[int(k) for k in restored], purchase_item__isnull=False)
                         .values_list('pk', 'purchase_item__rate'))
            self.cost = sum((Decimal(str(rates.get(int(k), 0))) * q for k, q in restored.items()),
                            Decimal('0')).quantize(TWO)

            super().save(*args, **kwargs)

            # Negative Sale row: dashboard, P&L aur ledger apne aap net ho jaate hain
            Sale.objects.create(
                date=self.sales_return.date, name=bill.customer_name, invoice_mode=bill.invoice_mode,
                taxable_value=-self.taxable_value, gst=-self.gst, total=-self.total, sales_return_item=self,
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            product = Product.objects.select_for_update().filter(pk=self.invoice_item.product_id).first()
            if product is not None:
                if product.stock_qty < self.base_qty:
                    raise ValidationError('Is return ka maal ab stock me nahi hai (dobara bik chuka), '
                                          'isliye return delete nahi ho sakta.')
                batches = []
                for key, q in (self.batch_returns or {}).items():
                    batch = Batch.objects.select_for_update().filter(pk=int(key)).first()
                    if batch is not None:
                        if batch.qty < q:
                            raise ValidationError('Is return ka maal ab batch me nahi hai (dobara bik chuka), '
                                                  'isliye return delete nahi ho sakta.')
                        batches.append((batch, q))
                product.stock_qty -= self.base_qty
                product.save(update_fields=['stock_qty'])
                for batch, q in batches:
                    batch.qty -= q
                    batch.save(update_fields=['qty'])
            super().delete(*args, **kwargs)   # Sale + Transaction cascade se hat jaate hain

    def __str__(self):
        return f"{self.invoice_item} (return {self.qty})"

    class Meta:
        verbose_name = "Sales Return Item"
        verbose_name_plural = "Sales Return Items"


# ======================================================
# PURCHASE RETURN (Debit Note)
# ======================================================

class PurchaseReturn(models.Model):
    note_number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    purchase = models.ForeignKey('Purchase', on_delete=models.PROTECT, related_name='returns')
    date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.note_number:
            self.note_number = next_doc_number('DN', self.date)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for item in self.items.all():
                item.delete()
            super().delete(*args, **kwargs)

    def _sum(self, field):
        return sum((getattr(i, field) or Decimal('0') for i in self.items.all()), Decimal('0'))

    @property
    def total(self):
        return self._sum('amount')

    @property
    def taxable(self):
        return self._sum('taxable_value')

    @property
    def total_items(self):
        return len(self.items.all())

    def __str__(self):
        return self.note_number or f"Purchase Return #{self.pk}"

    class Meta:
        verbose_name = "Purchase Return (Debit Note)"
        verbose_name_plural = "Purchase Returns (Debit Notes)"


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name='items')
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT, related_name='return_items')
    qty = models.IntegerField(verbose_name="Return Qty", help_text="Purchase me jis unit me aaya tha, usi me")
    base_qty = models.PositiveIntegerField(editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0,
                                 verbose_name="Amount (GST ke saath)")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Return item edit nahi hota. Galti ho to return delete karke naya banayein.')
        if not self.qty or self.qty <= 0:
            raise ValidationError('Return qty 0 se zyada honi chahiye.')

        with transaction.atomic():
            pi = PurchaseItem.objects.select_for_update().select_related('product', 'unit').get(pk=self.purchase_item_id)
            header = self.purchase_return.purchase
            if pi.purchase_id != header.pk:
                raise ValidationError('Ye item is purchase ka nahi hai.')

            previous = list(PurchaseReturnItem.objects.filter(purchase_item=pi))
            returned_qty = sum(p.qty for p in previous)
            if self.qty + returned_qty > pi.qty:
                raise ValidationError(
                    f"'{pi.product}' me sirf {pi.qty - returned_qty} {pi.unit or ''} wapas ho sakta hai.")

            price = Decimal(pi.purchase_price or 0)
            if self.qty + returned_qty == pi.qty:
                self.amount = price - sum((p.amount for p in previous), Decimal('0'))
                self.taxable_value = pi.tax - sum((p.taxable_value for p in previous), Decimal('0'))
                self.gst = pi.gst - sum((p.gst for p in previous), Decimal('0'))
            else:
                ratio = Decimal(self.qty) / Decimal(pi.qty)
                self.amount = (price * ratio).quantize(TWO)
                self.taxable_value = (pi.tax * ratio).quantize(TWO)
                self.gst = (pi.gst * ratio).quantize(TWO)
            self.base_qty = self.qty * (pi.unit_factor or 1)

            batch = Batch.objects.select_for_update().filter(purchase_item=pi).first()
            if batch is None or batch.qty < self.base_qty:
                have = batch.qty if batch else 0
                raise ValidationError(
                    f"'{pi.product}' ka itna maal batch me nahi bacha (bacha: {have}, wapas karna: {self.base_qty}). "
                    f"Baaki bik/kharch ho chuka hai.")
            product = Product.objects.select_for_update().get(pk=pi.product_id)
            if product.stock_qty < self.base_qty:
                raise ValidationError(f"'{product}' ka stock kam hai, return nahi ho sakta.")

            product.stock_qty -= self.base_qty
            product.save(update_fields=['stock_qty'])
            batch.qty -= self.base_qty
            batch.save(update_fields=['qty'])

            adjust_supplier_balance(header.supplier_id, -self.amount)   # supplier ko ab itna kam dena hai
            super().save(*args, **kwargs)
            Transaction.objects.create(
                purchase_return_item=self, purchase_date=self.purchase_return.date,
                supplier=header.supplier.company, purchase_amount=-self.amount,
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            pi = self.purchase_item
            product = Product.objects.select_for_update().get(pk=pi.product_id)
            product.stock_qty += self.base_qty
            product.save(update_fields=['stock_qty'])
            batch = Batch.objects.select_for_update().filter(purchase_item=pi).first()
            if batch is not None:
                batch.qty += self.base_qty
                batch.save(update_fields=['qty'])
            adjust_supplier_balance(pi.purchase.supplier_id, self.amount)
            super().delete(*args, **kwargs)   # Transaction cascade se hat jata hai

    def __str__(self):
        return f"{self.purchase_item} (return {self.qty})"

    class Meta:
        verbose_name = "Purchase Return Item"
        verbose_name_plural = "Purchase Return Items"


# ======================================================
# STOCK WRITE-OFF (expired / damaged)
# ======================================================

class StockWriteOff(models.Model):
    REASONS = (
        ('EXPIRED', 'Expired'),
        ('DAMAGED', 'Damaged / toota-phoota'),
        ('OTHER', 'Other'),
    )
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='writeoffs')
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="Base unit me (jaise Tablet)")
    reason = models.CharField(max_length=10, choices=REASONS, default='EXPIRED')
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True, default='')
    cost = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0,
                               help_text="Is maal ki purchase cost (P&L me loss)")

    def clean(self):
        if self.batch_id and self.qty and not self.pk and self.qty > self.batch.qty:
            raise ValidationError({'qty': f'Is batch me sirf {self.batch.qty} bacha hai.'})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Write-off edit nahi hota. Galti ho to delete karke naya banayein.')
        with transaction.atomic():
            batch = Batch.objects.select_for_update().select_related('purchase_item').get(pk=self.batch_id)
            if not self.qty or self.qty <= 0 or self.qty > batch.qty:
                raise ValidationError(f'Qty 1 se {batch.qty} ke beech honi chahiye.')
            product = Product.objects.select_for_update().get(pk=batch.product_id)
            if product.stock_qty < self.qty:
                raise ValidationError(f"'{product}' ka stock kam hai, write-off nahi ho sakta.")
            rate = batch.purchase_item.rate if batch.purchase_item_id else Decimal('0')
            self.cost = (Decimal(rate) * self.qty).quantize(TWO)
            batch.qty -= self.qty
            batch.save(update_fields=['qty'])
            product.stock_qty -= self.qty
            product.save(update_fields=['stock_qty'])
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=self.batch_id)
            batch.qty += self.qty
            batch.save(update_fields=['qty'])
            product = Product.objects.select_for_update().get(pk=batch.product_id)
            product.stock_qty += self.qty
            product.save(update_fields=['stock_qty'])
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.batch} x {self.qty} ({self.get_reason_display()})"

    class Meta:
        verbose_name = "Stock Write-off"
        verbose_name_plural = "Stock Write-offs"


# ======================================================
# PHYSICAL STOCK AUDIT
# ======================================================

class StockAudit(models.Model):
    STATUS_CHOICES = (
        ('OPEN', 'Open (ginti chal rahi hai)'),
        ('APPLIED', 'Applied (stock adjust ho gaya)'),
    )
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def start(cls, note=''):
        """Naya audit: sab active products ka abhi ka system stock snapshot."""
        with transaction.atomic():
            audit = cls.objects.create(note=note)
            products = Product.objects.exclude(is_active=False).order_by('name')
            StockAuditItem.objects.bulk_create([
                StockAuditItem(audit=audit, product=p, system_qty=p.stock_qty) for p in products
            ])
        return audit

    def apply(self):
        """
        Jin products ki ginti (counted) bhari hai unka stock ginti ke barabar kar do.
        Diff abhi ke stock se nikalta hai (snapshot ke baad bikri hui ho to bhi sahi rahe).
        Kam ginti: batches se (jo pehle expire ho) ghatata hai. Zyada ginti: 'ADJ-' naya batch banata hai.
        """
        if self.status == 'APPLIED':
            raise ValidationError('Ye audit pehle hi apply ho chuka hai.')
        with transaction.atomic():
            for item in self.items.select_related('product'):
                if item.counted_qty is None:
                    continue
                product = Product.objects.select_for_update().get(pk=item.product_id)
                diff = item.counted_qty - product.stock_qty
                item.system_qty_at_apply = product.stock_qty
                item.applied_diff = diff
                item.save(update_fields=['system_qty_at_apply', 'applied_diff'])
                if diff < 0:
                    remaining = -diff
                    batches = (Batch.objects.select_for_update().filter(product=product, qty__gt=0)
                               .order_by(F('expire_date').asc(nulls_last=True), 'id'))
                    for batch in batches:
                        if remaining <= 0:
                            break
                        take = min(batch.qty, remaining)
                        batch.qty -= take
                        batch.save(update_fields=['qty'])
                        remaining -= take
                elif diff > 0:
                    Batch.objects.create(
                        product=product, qty=diff, unit=product.unit,
                        batch_number=f"ADJ-{self.pk}-{product.pk}",
                    )
                product.stock_qty = item.counted_qty
                product.save(update_fields=['stock_qty'])
            self.status = 'APPLIED'
            self.save(update_fields=['status'])

    def __str__(self):
        return f"Stock Audit #{self.pk} ({self.date})"

    class Meta:
        verbose_name = "Stock Audit"
        verbose_name_plural = "Stock Audits"


class StockAuditItem(models.Model):
    audit = models.ForeignKey(StockAudit, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    system_qty = models.PositiveIntegerField(help_text="Audit shuru hote waqt system me stock (base unit)")
    counted_qty = models.PositiveIntegerField(null=True, blank=True, help_text="Asli ginti (base unit)")
    system_qty_at_apply = models.PositiveIntegerField(null=True, blank=True, editable=False)
    applied_diff = models.IntegerField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['audit', 'product'], name='unique_product_per_audit'),
        ]
