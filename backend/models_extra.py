"""
Newer models: Customer + credit receipts, Sales/Purchase Return (credit/debit note),
Stock write-off, Physical stock audit.

These are imported at the end of `backend/models.py` (so Django treats them as models of this app).
All stock/ledger effects live in the model's save()/delete(), so the UI and admin behave the same.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from .models import (
    TWO, Batch, Expense, InvoiceItem, Product, PurchaseItem, Sale, Transaction,
    adjust_supplier_balance, fiscal_year,
)


# ======================================================
# DOCUMENT NUMBERS (Credit Note / Debit Note)
# ======================================================

class DocCounter(models.Model):
    """A gap-free counter per series (CN, DN) for each financial year."""
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
    """The next number, like CN/2026-27/0001 (works the same way as the invoice counter)."""
    fy = fiscal_year(on)
    with transaction.atomic():
        counter, _ = DocCounter.objects.get_or_create(series=series, fiscal_year=fy)
        DocCounter.objects.filter(pk=counter.pk).update(last_number=F('last_number') + 1)
        counter.refresh_from_db()
    return f"{series}/{fy}/{counter.last_number:04d}"


# ======================================================
# CUSTOMER + CREDIT LEDGER
# ======================================================

class Customer(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    gstin = models.CharField(max_length=15, blank=True, null=True, verbose_name="GSTIN",
                             help_text="For B2B bills (optional)")
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                       help_text="Credit limit. 0 = no limit")
    is_active = models.BooleanField(default=True)
    created_at = models.DateField(auto_now_add=True, null=True)

    def clean(self):
        if self.gstin:
            self.gstin = self.gstin.strip().upper()
            if len(self.gstin) != 15:
                raise ValidationError({'gstin': 'GSTIN must be 15 characters.'})
        if self.credit_limit is not None and self.credit_limit < 0:
            raise ValidationError({'credit_limit': 'Limit cannot be negative.'})

    def save(self, *args, **kwargs):
        self.gstin = (self.gstin or '').strip().upper() or None
        super().save(*args, **kwargs)

    def balance(self, exclude_payment=None):
        """
        How much is still to be collected from the customer:
        (due portion of credit bills) - (receipts) - (returns adjusted against credit).
        This is always calculated (not stored), so it can never go out of sync.
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
        verbose_name = '17. Customer'
        verbose_name_plural = '17. Customers'


class CustomerPayment(models.Model):
    """Payment received back from the customer against credit (receipt)."""
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
            raise ValidationError({'amount': 'Amount must be greater than 0.'})
        if self.customer_id and self.amount:
            balance = self.customer.balance(exclude_payment=self.pk)
            if self.amount > balance:
                raise ValidationError({'amount': f'Amount exceeds the outstanding credit (₹{balance}).'})

    def __str__(self):
        return f"Receipt #{self.pk} - {self.customer} ₹{self.amount}"

    class Meta:
        verbose_name = '18. Customer Payment'
        verbose_name_plural = '18. Customer Payments'


# ======================================================
# SALES RETURN (Credit Note)
# ======================================================

class SalesReturn(models.Model):
    REFUND_CHOICES = (
        ('CASH', 'Refunded in cash / UPI'),
        ('ADJUST', 'Adjust against customer credit'),
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
                raise ValidationError({'bill': 'A return cannot be created for a cancelled bill.'})
            if self.refund_mode == 'ADJUST' and not self.bill.customer_id:
                raise ValidationError({'refund_mode': 'To adjust against credit, the bill must have a Customer.'})

    def save(self, *args, **kwargs):
        if not self.note_number:
            self.note_number = next_doc_number('CN', self.date)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for item in self.items.all():
                item.delete()   # stock/batch/sale reversed
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
        verbose_name = '19. Sales Return (Credit Note)'
        verbose_name_plural = '19. Sales Returns (Credit Notes)'


class SalesReturnItem(models.Model):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name='items')
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.PROTECT, related_name='return_items')
    qty = models.IntegerField(verbose_name="Return Qty", help_text="In the same unit used on the bill")
    base_qty = models.PositiveIntegerField(editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    igst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    batch_returns = models.JSONField(default=dict, editable=False, blank=True)   # {batch_id: base qty}
    cost = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)   # purchase cost of the returned goods

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('A return item cannot be edited. If there is a mistake, delete the return and create a new one.')
        if not self.qty or self.qty <= 0:
            raise ValidationError('Return qty must be greater than 0.')

        with transaction.atomic():
            item = InvoiceItem.objects.select_for_update().select_related('product', 'unit').get(pk=self.invoice_item_id)
            bill = self.sales_return.bill
            if item.bill_id != bill.pk:
                raise ValidationError('This item does not belong to this bill.')
            if not item.product_id or not item.qty:
                raise ValidationError('This bill item has no product/qty.')

            previous = list(SalesReturnItem.objects.filter(invoice_item=item))
            returned_qty = sum(p.qty for p in previous)
            if self.qty + returned_qty > item.qty:
                raise ValidationError(
                    f"Only {item.qty - returned_qty} {item.unit or ''} of '{item.product}' can be returned.")

            # ---- Amounts (the last portion = the remainder, so no rounding paise are left over) ----
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

            # ---- Restore stock + batch (into the batch it came from, latest first) ----
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
            if remaining > 0:   # older bills that have no batch history
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

            # Negative Sale row: dashboard, P&L and ledger net out automatically
            Sale.objects.create(
                date=self.sales_return.date, name=bill.customer_name, invoice_mode=bill.invoice_mode,
                taxable_value=-self.taxable_value, gst=-self.gst, total=-self.total, sales_return_item=self,
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            product = Product.objects.select_for_update().filter(pk=self.invoice_item.product_id).first()
            if product is not None:
                if product.stock_qty < self.base_qty:
                    raise ValidationError('The goods from this return are no longer in stock (sold again), '
                                          'so the return cannot be deleted.')
                batches = []
                for key, q in (self.batch_returns or {}).items():
                    batch = Batch.objects.select_for_update().filter(pk=int(key)).first()
                    if batch is not None:
                        if batch.qty < q:
                            raise ValidationError('The goods from this return are no longer in the batch (sold again), '
                                                  'so the return cannot be deleted.')
                        batches.append((batch, q))
                product.stock_qty -= self.base_qty
                product.save(update_fields=['stock_qty'])
                for batch, q in batches:
                    batch.qty -= q
                    batch.save(update_fields=['qty'])
            super().delete(*args, **kwargs)   # Sale + Transaction are removed by cascade

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
        verbose_name = '20. Purchase Return (Debit Note)'
        verbose_name_plural = '20. Purchase Returns (Debit Notes)'


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name='items')
    purchase_item = models.ForeignKey(PurchaseItem, on_delete=models.PROTECT, related_name='return_items')
    qty = models.IntegerField(verbose_name="Return Qty", help_text="In the same unit used on the purchase")
    base_qty = models.PositiveIntegerField(editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0,
                                 verbose_name="Amount (incl. GST)")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('A return item cannot be edited. If there is a mistake, delete the return and create a new one.')
        if not self.qty or self.qty <= 0:
            raise ValidationError('Return qty must be greater than 0.')

        with transaction.atomic():
            pi = PurchaseItem.objects.select_for_update().select_related('product', 'unit').get(pk=self.purchase_item_id)
            header = self.purchase_return.purchase
            if pi.purchase_id != header.pk:
                raise ValidationError('This item does not belong to this purchase.')

            previous = list(PurchaseReturnItem.objects.filter(purchase_item=pi))
            returned_qty = sum(p.qty for p in previous)
            if self.qty + returned_qty > pi.qty:
                raise ValidationError(
                    f"Only {pi.qty - returned_qty} {pi.unit or ''} of '{pi.product}' can be returned.")

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
                    f"Not enough of '{pi.product}' is left in the batch (available: {have}, to return: {self.base_qty}). "
                    f"The rest has already been sold/used.")
            product = Product.objects.select_for_update().get(pk=pi.product_id)
            if product.stock_qty < self.base_qty:
                raise ValidationError(f"Insufficient stock of '{product}'; the return cannot be made.")

            product.stock_qty -= self.base_qty
            product.save(update_fields=['stock_qty'])
            batch.qty -= self.base_qty
            batch.save(update_fields=['qty'])

            adjust_supplier_balance(header.supplier_id, -self.amount)   # the supplier is now owed this much less
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
            super().delete(*args, **kwargs)   # the Transaction is removed by cascade

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
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="In base unit (e.g. Tablet)")
    reason = models.CharField(max_length=10, choices=REASONS, default='EXPIRED')
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True, default='')
    cost = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0,
                               help_text="Purchase cost of these goods (loss in P&L)")

    def clean(self):
        if self.batch_id and self.qty and not self.pk and self.qty > self.batch.qty:
            raise ValidationError({'qty': f'This batch has only {self.batch.qty} left.'})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('A write-off cannot be edited. If there is a mistake, delete it and create a new one.')
        with transaction.atomic():
            batch = Batch.objects.select_for_update().select_related('purchase_item').get(pk=self.batch_id)
            if not self.qty or self.qty <= 0 or self.qty > batch.qty:
                raise ValidationError(f'Qty must be between 1 and {batch.qty}.')
            product = Product.objects.select_for_update().get(pk=batch.product_id)
            if product.stock_qty < self.qty:
                raise ValidationError(f"Insufficient stock of '{product}'; the write-off cannot be made.")
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
        verbose_name = '21. Stock Write-off'
        verbose_name_plural = '21. Stock Write-offs'


# ======================================================
# PHYSICAL STOCK AUDIT
# ======================================================

class StockAudit(models.Model):
    STATUS_CHOICES = (
        ('OPEN', 'Open (counting in progress)'),
        ('APPLIED', 'Applied (stock adjusted)'),
    )
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def start(cls, note=''):
        """New audit: a snapshot of the current system stock of all active products."""
        with transaction.atomic():
            audit = cls.objects.create(note=note)
            products = Product.objects.exclude(is_active=False).order_by('name')
            StockAuditItem.objects.bulk_create([
                StockAuditItem(audit=audit, product=p, system_qty=p.stock_qty) for p in products
            ])
        return audit

    def apply(self):
        """
        Set the stock of every product that has a counted quantity to match that count.
        The difference is computed from the current stock (so it stays correct even if sales happened after the snapshot).
        Lower count: reduces from batches (earliest expiry first). Higher count: creates a new 'ADJ-' batch.
        """
        if self.status == 'APPLIED':
            raise ValidationError('This audit has already been applied.')
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
        verbose_name = '22. Stock Audit'
        verbose_name_plural = '22. Stock Audits'


class StockAuditItem(models.Model):
    audit = models.ForeignKey(StockAudit, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    system_qty = models.PositiveIntegerField(help_text="System stock when the audit started (base unit)")
    counted_qty = models.PositiveIntegerField(null=True, blank=True, help_text="Counted quantity (base unit)")
    system_qty_at_apply = models.PositiveIntegerField(null=True, blank=True, editable=False)
    applied_diff = models.IntegerField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['audit', 'product'], name='unique_product_per_audit'),
        ]


# ======================================================
# AUDIT LOG (who changed what)
# ======================================================

class AuditLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.CharField(max_length=150, blank=True, default='')
    action = models.CharField(max_length=20)
    target = models.CharField(max_length=60, blank=True, default='')
    detail = models.CharField(max_length=500, blank=True, default='')

    def __str__(self):
        return f"{self.user}: {self.action} {self.target}"

    class Meta:
        ordering = ['-id']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'


# ======================================================
# EMPLOYEES + FIXED (RECURRING) EXPENSES
# ======================================================

class Employee(models.Model):
    """A staff member. The salary is added to Expenses automatically every month (see recurring.py)."""
    code = models.CharField(max_length=20, unique=True, editable=False, blank=True, verbose_name="Employee ID")
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, default='', help_text="e.g. Pharmacist, Helper")
    joining_date = models.DateField(default=timezone.localdate)
    leaving_date = models.DateField(null=True, blank=True, help_text="Leave empty while the employee is working")
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    pay_day = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text="Day of the month the salary is paid (1-28)")
    auto_salary = models.BooleanField(default=True, verbose_name="Add salary automatically every month")
    is_active = models.BooleanField(default=True)
    last_salary_period = models.DateField(null=True, blank=True, editable=False)

    def clean(self):
        if self.monthly_salary is not None and self.monthly_salary < 0:
            raise ValidationError({'monthly_salary': 'Salary cannot be negative.'})
        if self.leaving_date and self.joining_date and self.leaving_date < self.joining_date:
            raise ValidationError({'leaving_date': 'Leaving date cannot be before the joining date.'})

    def save(self, *args, **kwargs):
        if not self.code:
            n = (Employee.objects.aggregate(m=Max('id'))['m'] or 0) + 1
            while Employee.objects.filter(code=f"EMP-{n:03d}").exists():
                n += 1
            self.code = f"EMP-{n:03d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"

    class Meta:
        verbose_name = '23. Employee'
        verbose_name_plural = '23. Employees'


class RecurringExpense(models.Model):
    """A fixed expense that repeats every month (rent, internet ...). One Expense entry is added automatically."""
    title = models.CharField(max_length=255, help_text="e.g. Shop rent")
    name = models.CharField(max_length=255, choices=Expense.EXPENSES_TYPES, verbose_name="Type")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    day_of_month = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text="The entry is added on this day of every month (1-28)")
    start_date = models.DateField(default=timezone.localdate, help_text="The first entry is added on or after this date")
    end_date = models.DateField(null=True, blank=True, help_text="Optional: stop adding entries after this date")
    is_active = models.BooleanField(default=True)
    last_period = models.DateField(null=True, blank=True, editable=False)

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': 'Amount must be greater than 0.'})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before the start date.'})

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = '24. Fixed Expense'
        verbose_name_plural = '24. Fixed Expenses'


# ======================================================
# IMPORT FILE LAYOUTS (remembered column matching)
# ======================================================

class ImportLayout(models.Model):
    """How the columns of a particular file layout (usually one supplier's export) were matched to our columns.
    Found again by the set of headers, so the next file in the same layout needs no matching."""
    kind = models.CharField(max_length=20)
    signature = models.CharField(max_length=40)
    mapping = models.TextField(default='{}', help_text='JSON: file header -> our column ("" = ignored)')
    headers = models.TextField(blank=True, default='')
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('kind', 'signature')
        verbose_name = '26. Import file layout'
        verbose_name_plural = '26. Import file layouts'

    def __str__(self):
        return f"{self.kind}: {self.headers[:60]}"


# ======================================================
# HELD BILLS (a bill in progress that is parked and opened again later)
# ======================================================

class HeldBill(models.Model):
    """What was typed on the new-bill screen, parked. No stock is touched; the bill is created only when it is saved after resuming."""
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.CharField(max_length=150, blank=True, default='')
    label = models.CharField(max_length=150, blank=True, default='')
    items_count = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=list)          # [[field name, value], ...] of the bill form

    def __str__(self):
        return f"Held bill #{self.pk}: {self.label}"

    class Meta:
        ordering = ['-id']
        verbose_name = 'Held Bill'
        verbose_name_plural = 'Held Bills'


# ======================================================
# HEALTH SNAPSHOTS (the health score as it was measured at a time)
# ======================================================

class HealthSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    score = models.PositiveIntegerField(default=0)
    data = models.JSONField(default=dict)

    def __str__(self):
        return f"Health {self.score}/100 at {self.created_at:%d %b %H:%M}"

    class Meta:
        ordering = ['-id']
        verbose_name = 'Health Snapshot'
        verbose_name_plural = 'Health Snapshots'


# ======================================================
# BLOG (public articles on the website; written in the admin)
# ======================================================

class BlogPost(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True, help_text="The address of the article: /blog/<slug>/. Use lower-case words with dashes.")
    summary = models.CharField(max_length=300, help_text="One or two sentences. Shown in the article list and used as the description on Google.")
    body = models.TextField(help_text="Plain text. Blank line = new paragraph. '## Heading', '### Smaller heading', '- ' for a bullet list, "
                                       "'1. ' for a numbered list, **bold**, [link text](https://...).")
    published = models.BooleanField(default=False, help_text="Tick to show the article on the website. Untick to keep it as a draft.")
    published_at = models.DateTimeField(default=timezone.now, help_text="Shown as the article date. A future date keeps it hidden until then.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-id']
        verbose_name = 'Blog Article'
        verbose_name_plural = 'Blog Articles'

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/blog/{self.slug}/'

    @classmethod
    def live(cls):
        """Articles visitors may see: ticked as published and already dated."""
        return cls.objects.filter(published=True, published_at__lte=timezone.now())
