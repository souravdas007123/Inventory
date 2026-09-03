from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Q
from import_export.admin import ImportExportModelAdmin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Supplier,Product,Godown,Purchase,Sale,Order,InvoiceItem,Category,Brand,Payment,Unit,Batch,Transaction,Bill


@admin.register(Category)
class CategoryAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    list_per_page = 12


@admin.register(Brand)
class BrandAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    list_per_page = 12


@admin.register(Unit)
class UnitAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name')
    list_per_page = 12


@admin.register(Supplier)
class SupplierAdmin(ImportExportModelAdmin):
    list_display = ('id', 'company','address','gstin','state', 'pan', 'opening_balance', 'created_at','is_active')
    list_per_page = 12
    
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'amount','due', 'payment_mode', 'payment_date')
    list_per_page = 12
    def delete_queryset(self, request, queryset):
            for obj in queryset:
                obj.delete()

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'purchase_date','supplier','purchase_amount','sale_date','customer','sale_amount')
    list_per_page = 12

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
    list_display = ('id', 'name','brand','hsn_code','sku', 'category', 'batch','cost_price','unit', 'gst_rate', 'stock_qty', 'stock_status', 'is_active' )
    list_per_page = 12

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
    list_display = ('id', 'product','supplier','qty','unit','batch_number', 'manufacture_date','expire_date','get_expiry_status')
    list_per_page = 12
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


@admin.register(Godown)
class GodownAdmin(ImportExportModelAdmin):
    list_display = ('id', 'name', 'location')
    list_per_page = 12


@admin.register(Purchase)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_date','supplier', 'godown','product','batch','manufacture_date','expire_date','rate','qty','unit','tax','gst_rate','cgst','sgst','purchase_price' )
    list_per_page = 12
    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


@admin.register(Sale)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = ('id','date', 'name', 'invoice_mode','taxable_value', 'gst','total')
    list_per_page = 12

@admin.register(Order)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'order_date','order_id','customers_name', 'customer_product', 'customer_rate','customer_qty')
    list_per_page = 12

  
class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0  # Default kitni blank rows dikhani hain 
    # list_display = ('id', 'order','product', 'rate','qty','unit','taxable_value','gst_rate','cgst','sgst','igst','total')
    readonly_fields = ('taxable_value', 'cgst', 'sgst', 'igst', 'total')
    list_per_page = 12
    
    def delete_queryset(self, request, queryset):
            for obj in queryset:
                obj.delete()
                
    class Media:
        # Yeh line batati hai ki admin page par kaunsi JS file load karni hai
        js = ('js/invoice_toggle.js',)

    # Aapka purana formfield_for_foreignkey wala function yahan rahega
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # ... (purana code jo order id hide karne ke liye likha tha)
        pass

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order":
            # Check karte hain ki user naya invoice bana raha hai ya purana edit kar raha hai
            object_id = request.resolver_match.kwargs.get('object_id')
            
            if object_id:
                # EDIT MODE: 
                # Hum current invoice ka data nikalenge
                try:
                    current_invoice = InvoiceItem.objects.get(pk=object_id)
                    if current_invoice.order:
                        # Sirf wo orders dikhayein jo abhi tak use nahi huye hain (isnull=True) 
                        # YA FIR (Q) jo is current invoice ka order hai
                        kwargs["queryset"] = Order.objects.filter(
                            Q(invoiceitem__isnull=True) | Q(id=current_invoice.order.id)
                        )
                    else:
                        kwargs["queryset"] = Order.objects.filter(invoiceitem__isnull=True)
                except InvoiceItem.DoesNotExist:
                    kwargs["queryset"] = Order.objects.filter(invoiceitem__isnull=True)
            
            else:
                # ADD MODE (Naya Invoice): 
                # Sirf wo orders dikhayein jinse abhi tak koi InvoiceItem link nahi hua hai
                kwargs["queryset"] = Order.objects.filter(invoiceitem__isnull=True)
                
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'date','taxable','cgst','sgst','igst','total')
    inlines = [InvoiceItemInline] # Items ko bill ke niche attach karne ke liye

    



  
