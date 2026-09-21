from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Q
from import_export.admin import ImportExportModelAdmin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from django.template.response import TemplateResponse
from django.db.models import Sum
from datetime import date
from .models import Supplier,Product,Purchase,Sale,Order,InvoiceItem,Category,Brand,Payment,Unit,Batch,Transaction,Bill,Expense, FinancialReport,PurchaseOrder, PurchaseOrderItem


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
    list_display = ('id', 'company','address','gstin','state', 'pan', 'opening_balance', 'created_at','is_active')
    search_fields = ['company', 'gstin']
    list_per_page = 10
    
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'amount','due', 'payment_mode', 'payment_date')
    search_fields = ['supplier']
    list_per_page = 10
    def delete_queryset(self, request, queryset):
            for obj in queryset:
                obj.delete()

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'purchase_date','supplier','purchase_amount','sale_date','customer','sale_amount')
    search_fields = ['supplier', 'customer']
    list_per_page = 10

class ProductResource(resources.ModelResource):
    
    category = fields.Field(
        column_name='category',
        attribute='category',
        widget=ForeignKeyWidget(Category, field='name') 
    )
    
    brand = fields.Field(
        column_name='brand',
        attribute='brand',
        widget=ForeignKeyWidget(Brand, field='name') 
    )

    unit = fields.Field(
            column_name='unit',
            attribute='unit',
            widget=ForeignKeyWidget(Unit, field='name') 
        )

    class Meta:
        model = Product   
    
@admin.register(Product)
class ProductAdmin(ImportExportModelAdmin):
    resource_classes = [ProductResource]
    list_display = ('id', 'name','composition','brand','hsn_code','sku', 'category', 'batch','rack', 'row','unit', 'gst_rate', 'stock_qty', 'stock_status' )
    search_fields = ['name', 'sku', 'rack']
    list_per_page = 10

    def stock_status(self, obj):
        low_stock_threshold = 5

        current_qty = obj.stock_qty 

        if current_qty == 0:
            # FIX: Text ko {} ke through pass kiya gaya hai
            return format_html(
                '<span style="color: red; font-weight: bold;">{}</span>',
                '❌ Out of Stock'
            )
        elif current_qty <= low_stock_threshold:
            # Ye pehle se theek tha kyunki isme 'current_qty' pass ho raha tha
            return format_html(
                '<span style="color: #D4AC0D; font-weight: bold;">⚠️ Low Stock ({} left)</span>',
                current_qty
            )
        else:
            # FIX: Text ko {} ke through pass kiya gaya hai
            return format_html(
                '<span style="color: #51D916; font-weight: bold;">{}</span>',
                '✅ In Stock'
            )
        
    stock_status.short_description = 'Status'


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'product','supplier','qty','unit','batch_number','rack','row','location', 'manufacture_date','expire_date','get_expiry_status')
    search_fields = ['product', 'supplier']
    list_per_page = 10
    def get_expiry_status(self, obj):
        status = obj.expiry_status
        
        # Status ke basis par color define karein
        if status == "Safe":
            color = "#51D916"
        elif status == "Expiring Soon":
            # Yellow white background par padhne mein dikkat karta hai, isliye dark yellow/amber use kar rahe hain
            color = "#D4AC0D" 
        elif status == "Expired":
            color = "red"
        else:
            color = "black"

        # HTML render karna
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, status
        )
    
    # Admin panel ki column heading ka naam set karne ke liye
    get_expiry_status.short_description = 'Status'


@admin.register(Purchase)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_date','supplier','product','composition','batch','rack', 'row','location', 'manufacture_date','expire_date','rate','qty','unit','tax','gst_rate','cgst','sgst','purchase_price' )
    search_fields = ['supplier', 'product__name', 'rack','composition']
    list_per_page = 10
    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


@admin.register(Sale)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = ('id','date', 'name', 'invoice_mode','taxable_value', 'gst','total')
    search_fields = ['name']
    list_per_page = 10

@admin.register(Order)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_date','order_id','customers_name', 'customer_product', 'customer_rate','customer_qty')
    search_fields = ['customers_name']
    list_per_page = 10

  
class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0  # Default kitni blank rows dikhani hain 
    readonly_fields = ('taxable_value', 'cgst', 'sgst', 'igst', 'total')
    exclude = ['batch_history']
    list_per_page = 10
    
    

@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'invoice_mode', 'order','date','total_items','taxable','cgst','sgst','igst','total')
    inlines = [InvoiceItemInline] # Items ko bill ke niche attach karne ke liye
    list_per_page = 10


    class Media:
        js = ('js/invoice_toggle.js',)

    def delete_queryset(self, request, queryset):
        """
        Jab bulk delete action trigger ho, toh direct SQL delete ki jagah 
        har bill par loop chalakar uska apna delete() function call karein.
        """
        for bill in queryset:
            bill.delete()    

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order":
            object_id = request.resolver_match.kwargs.get('object_id')
            
            if object_id:
                # EDIT MODE
                try:
                    current_bill = Bill.objects.get(pk=object_id)
                    if current_bill.order:
                        # Q(bill__isnull=True) check karta hai ki order kisi aur bill se attached na ho
                        kwargs["queryset"] = Order.objects.filter(
                            Q(bill__isnull=True) | Q(id=current_bill.order.id)
                        )
                    else:
                        kwargs["queryset"] = Order.objects.filter(bill__isnull=True)
                except Bill.DoesNotExist:
                    kwargs["queryset"] = Order.objects.filter(bill__isnull=True)
            else:
                # ADD MODE (Naya Invoice)
                kwargs["queryset"] = Order.objects.filter(bill__isnull=True)
                
        return super().formfield_for_foreignkey(db_field, request, **kwargs)



@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['name', 'amount', 'date']

# Report Button ka logic
@admin.register(FinancialReport)
class FinancialReportAdmin(admin.ModelAdmin):
    
    # Jab admin mein 'P&L Report' par click hoga, tab yeh function chalega
    def changelist_view(self, request, extra_context=None):
        current_month = date.today().month
        current_year = date.today().year

        # 1. Total Income (Iss mahine ki saari Sales)
        sales = Sale.objects.filter(
            date__month=current_month, date__year=current_year
        ).aggregate(Sum('total'))['total__sum'] or 0

        # 2. Total Direct Expense (Iss mahine ka saara Purchase)
        purchases = Purchase.objects.filter(
            order_date__month=current_month, order_date__year=current_year
        ).aggregate(Sum('purchase_price'))['purchase_price__sum'] or 0

        # 3. Total Indirect Expense (Rent, light bill, etc.)
        expenses = Expense.objects.filter(
            date__month=current_month, date__year=current_year
        ).aggregate(Sum('amount'))['amount__sum'] or 0

        # Calculation
        gross_profit = sales - purchases
        net_profit = gross_profit - expenses

        # Template ke liye data bhejna
        context = dict(
            self.admin_site.each_context(request),
            title=f"Profit & Loss Report ({date.today().strftime('%B %Y')})",
            sales=sales,
            purchases=purchases,
            expenses=expenses,
            gross_profit=gross_profit,
            net_profit=net_profit,
        )
        
        # Yeh HTML design file ko load karega (jo hum step 3 mein banayenge)
        return TemplateResponse(request, "admin/financial_report.html", context)
    

class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0  # Faltu ke khali rows na dikhaye
    fields = ('product', 'order_qty')
    
# Main Purchase Order ka Admin panel
@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('po_number', 'supplier', 'date_created', 'status')
    list_filter = ('status', 'date_created', 'supplier')
    search_fields = ('po_number', 'supplier__company')
    readonly_fields = ('po_number', 'date_created')
    inlines = [PurchaseOrderItemInline]  # Items ko PO ke andar dikhane ke liye
    
    # Status change karne ke liye ek shortcut action (Optional)
    actions = ['mark_as_sent', 'mark_as_completed']

    def mark_as_sent(self, request, queryset):
        queryset.update(status='SENT')
    mark_as_sent.short_description = "Mark selected POs as SENT"

    def mark_as_completed(self, request, queryset):
        queryset.update(status='COMPLETED')
    mark_as_completed.short_description = "Mark selected POs as COMPLETED"


  
