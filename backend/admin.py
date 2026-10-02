import calendar
from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html
from import_export import fields, resources
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import (
    Batch, Bill, Brand, Category, Expense, FinancialReport, InvoiceItem, Order,
    Payment, Product, ProductUnit, Purchase, PurchaseItem, PurchaseOrder, PurchaseOrderItem, Sale,
    Supplier, Transaction, Unit,
)


class SafeDeleteMixin:
    """Bulk delete me bhi har object ka apna delete() chalega (stock/balance sahi rahe)."""

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


class ReadOnlyLedgerMixin:
    """Auto-generated records: admin se manually add nahi hone chahiye."""

    def has_add_permission(self, request):
        return False


# ======================================================
# MASTER DATA
# ======================================================

@admin.register(Category)
class CategoryAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    search_fields = ['name']
    list_per_page = 10


@admin.register(Brand)
class BrandAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    search_fields = ['name']
    list_per_page = 10


@admin.register(Unit)
class UnitAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    search_fields = ['name']
    list_per_page = 10


@admin.register(Supplier)
class SupplierAdmin(ImportExportModelAdmin):
    list_display = ('id', 'company', 'address', 'gstin', 'state', 'pan', 'opening_balance', 'created_at', 'is_active')
    search_fields = ['company', 'gstin']
    list_filter = ('is_active',)
    list_per_page = 10


# ======================================================
# PAYMENT / TRANSACTION
# ======================================================

@admin.register(Payment)
class PaymentAdmin(SafeDeleteMixin, admin.ModelAdmin):
    list_display = ('id', 'supplier', 'amount', 'due', 'payment_mode', 'payment_date')
    search_fields = ['supplier__company']
    list_select_related = ('supplier',)
    autocomplete_fields = ['supplier']
    list_per_page = 10


@admin.register(Transaction)
class TransactionAdmin(ReadOnlyLedgerMixin, admin.ModelAdmin):
    list_display = ('id', 'purchase_date', 'supplier', 'purchase_amount', 'sale_date', 'customer', 'sale_amount')
    search_fields = ['supplier', 'customer']
    list_per_page = 10


# ======================================================
# PRODUCT
# ======================================================

class ProductResource(resources.ModelResource):
    category = fields.Field(
        column_name='category', attribute='category',
        widget=ForeignKeyWidget(Category, field='name'),
    )
    brand = fields.Field(
        column_name='brand', attribute='brand',
        widget=ForeignKeyWidget(Brand, field='name'),
    )
    unit = fields.Field(
        column_name='unit', attribute='unit',
        widget=ForeignKeyWidget(Unit, field='name'),
    )

    class Meta:
        model = Product


class ProductUnitInline(admin.TabularInline):
    model = ProductUnit
    extra = 1
    fields = ('unit', 'factor')


@admin.register(Product)
class ProductAdmin(ImportExportModelAdmin):
    inlines = [ProductUnitInline]
    resource_classes = [ProductResource]
    list_display = (
        'id', 'name', 'composition', 'brand', 'hsn_code', 'sku', 'category',
        'pick_batch', 'rack', 'row', 'unit', 'gst_rate', 'stock_qty', 'stock_packs',
        'stock_status', 'alternatives',
    )
    search_fields = ['name', 'sku', 'rack', 'composition']
    list_select_related = ('brand', 'category', 'unit')
    list_per_page = 10

    def stock_status(self, obj):
        qty = obj.stock_qty
        if qty == 0:
            return format_html('<span style="color: red; font-weight: bold;">{}</span>', '❌ Out of Stock')
        elif qty <= obj.reorder_level:
            return format_html(
                '<span style="color: #D4AC0D; font-weight: bold;">⚠️ Low Stock ({} left)</span>', qty
            )
        return format_html('<span style="color: #51D916; font-weight: bold;">{}</span>', '✅ In Stock')

    stock_status.short_description = 'Status'

    def stock_packs(self, obj):
        return obj.stock_display()

    stock_packs.short_description = 'Stock (Packs)'

    def get_readonly_fields(self, request, obj=None):
        # Stock/transactions ban chuke ho to base unit badalna khatarnak hai
        if obj and obj.pk and (
            obj.stock_qty > 0
            or PurchaseItem.objects.filter(product=obj).exists()
            or InvoiceItem.objects.filter(product=obj).exists()
        ):
            return ('unit',)
        return ()

    def pick_batch(self, obj):
        batch = obj.get_next_batch()
        if not batch:
            return '-'
        exp = batch.expire_date.strftime('%d-%b-%Y') if batch.expire_date else 'No EXP'
        return format_html('🟢 {} (Exp: {})', batch.batch_number, exp)

    pick_batch.short_description = 'Pick Batch (FEFO)'

    def alternatives(self, obj):
        if obj.stock_qty > 0:
            return '-'
        alts = obj.get_alternatives()
        if not alts:
            return '❌ No alternative'
        return ', '.join(f"{a.name} ({a.stock_qty})" for a in alts)

    alternatives.short_description = 'Alternative'


# ======================================================
# BATCH
# ======================================================

@admin.register(Batch)
class BatchAdmin(ReadOnlyLedgerMixin, admin.ModelAdmin):
    list_display = (
        'id', 'product', 'supplier', 'qty', 'unit', 'batch_number', 'rack', 'row',
        'location', 'manufacture_date', 'expire_date', 'get_expiry_status',
    )
    search_fields = ['product__name', 'supplier__company', 'batch_number']
    list_select_related = ('product', 'supplier', 'unit')
    list_per_page = 10

    def get_expiry_status(self, obj):
        status = obj.expiry_status
        colors = {"Safe": "#51D916", "Expiring Soon": "#D4AC0D", "Expired": "red"}
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(status, "black"), status,
        )

    get_expiry_status.short_description = 'Status'


# ======================================================
# PURCHASE
# ======================================================

class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1
    autocomplete_fields = ['product']
    fields = (
        'product', 'qty', 'unit', 'purchase_price', 'manufacture_date', 'expire_date',
        'composition', 'rack', 'row', 'location', 'base_qty', 'batch', 'tax', 'cgst', 'sgst',
    )
    readonly_fields = ('base_qty', 'batch', 'tax', 'cgst', 'sgst')


@admin.register(Purchase)
class PurchaseAdmin(SafeDeleteMixin, admin.ModelAdmin):
    list_display = ('id', 'invoice_number', 'invoice_date', 'supplier', 'total_items', 'taxable', 'gst_total', 'total')
    search_fields = ['invoice_number', 'supplier__company', 'items__product__name', 'items__batch']
    list_filter = ('invoice_date', 'supplier')
    list_select_related = ('supplier',)
    autocomplete_fields = ['supplier']
    inlines = [PurchaseItemInline]
    date_hierarchy = 'invoice_date'
    list_per_page = 10


# ======================================================
# SALE / ORDER
# ======================================================

@admin.register(Sale)
class SaleAdmin(ReadOnlyLedgerMixin, admin.ModelAdmin):
    list_display = ('id', 'date', 'name', 'invoice_mode', 'taxable_value', 'gst', 'total')
    search_fields = ['name']
    list_per_page = 10


@admin.register(Order)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_date', 'order_id', 'customers_name', 'customer_product', 'customer_rate', 'customer_qty', 'customer_unit')
    search_fields = ['customers_name', 'order_id']
    list_select_related = ('customer_product',)
    autocomplete_fields = ['customer_product']
    list_per_page = 10


# ======================================================
# BILL
# ======================================================

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ('base_qty', 'taxable_value', 'cgst', 'sgst', 'igst', 'total')
    exclude = ['batch_history']
    autocomplete_fields = ['product']


@admin.register(Bill)
class BillAdmin(SafeDeleteMixin, admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'invoice_mode', 'order', 'date', 'total_items', 'taxable', 'cgst', 'sgst', 'igst', 'total')
    inlines = [InvoiceItemInline]
    list_per_page = 10

    class Media:
        js = ('js/invoice_toggle.js',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order":
            object_id = request.resolver_match.kwargs.get('object_id')
            queryset = Order.objects.filter(bill__isnull=True)

            if object_id:
                current_bill = Bill.objects.filter(pk=object_id).first()
                if current_bill and current_bill.order_id:
                    queryset = Order.objects.filter(
                        Q(bill__isnull=True) | Q(id=current_bill.order_id)
                    )
            kwargs["queryset"] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ======================================================
# EXPENSE
# ======================================================

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['name', 'amount', 'date']
    list_filter = ['name', 'date']


# ======================================================
# PROFIT & LOSS REPORT
# ======================================================

def _dec(value):
    return Decimal(str(value or 0))


@admin.register(FinancialReport)
class FinancialReportAdmin(admin.ModelAdmin):

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied

        today = timezone.localdate()
        try:
            month = int(request.GET.get('month', today.month))
            year = int(request.GET.get('year', today.year))
        except (TypeError, ValueError):
            month, year = today.month, today.year
        if not 1 <= month <= 12:
            month = today.month

        # 1. Income = Sales ki taxable value (GST income nahi hai)
        sales = _dec(Sale.objects.filter(
            date__month=month, date__year=year
        ).aggregate(s=Sum('taxable_value'))['s'])

        # 2. COGS = jo maal bika uski purchase cost (batch_history se)
        batch_rate = {
            b.id: b.purchase_item.rate
            for b in Batch.objects.select_related('purchase_item').filter(purchase_item__isnull=False)
        }
        cogs = Decimal('0')
        items = InvoiceItem.objects.filter(sale__date__month=month, sale__date__year=year)
        for item in items:
            for batch_id, qty in (item.batch_history or {}).items():
                cogs += batch_rate.get(int(batch_id), Decimal('0')) * qty

        # Sirf information ke liye: is mahine kitna maal kharida (taxable)
        purchases = _dec(PurchaseItem.objects.filter(
            purchase__invoice_date__month=month, purchase__invoice_date__year=year
        ).aggregate(s=Sum('tax'))['s'])

        # 3. Indirect expenses
        expenses = _dec(Expense.objects.filter(
            date__month=month, date__year=year
        ).aggregate(s=Sum('amount'))['s'])

        gross_profit = sales - cogs
        net_profit = gross_profit - expenses

        context = dict(
            self.admin_site.each_context(request),
            title=f"Profit & Loss Report ({calendar.month_name[month]} {year})",
            sales=sales,
            cogs=cogs,
            purchases=purchases,
            expenses=expenses,
            gross_profit=gross_profit,
            net_profit=net_profit,
            month=month,
            year=year,
            months=[(i, calendar.month_name[i]) for i in range(1, 13)],
            years=list(range(today.year - 5, today.year + 1)),
        )
        return TemplateResponse(request, "admin/financial_report.html", context)


# ======================================================
# PURCHASE ORDER
# ======================================================

class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    fields = ('product', 'order_qty')
    autocomplete_fields = ['product']


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('po_number', 'supplier', 'date_created', 'status')
    list_filter = ('status', 'date_created', 'supplier')
    search_fields = ('po_number', 'supplier__company')
    readonly_fields = ('po_number', 'date_created')
    inlines = [PurchaseOrderItemInline]
    actions = ['mark_as_sent', 'mark_as_completed']

    @admin.action(description="Mark selected POs as SENT")
    def mark_as_sent(self, request, queryset):
        count = queryset.update(status='SENT')
        self.message_user(request, f"{count} PO(s) SENT mark ho gaye.", messages.SUCCESS)

    @admin.action(description="Mark selected POs as COMPLETED")
    def mark_as_completed(self, request, queryset):
        count = queryset.update(status='COMPLETED')
        self.message_user(request, f"{count} PO(s) COMPLETED mark ho gaye.", messages.SUCCESS)