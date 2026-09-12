from decimal import Decimal
from django.db import models,transaction
from django.contrib.auth.models import User
from django.db.models.aggregates import Count, Sum
from django.utils import timezone
from django.core.exceptions import ValidationError
import random
import string
from datetime import date, timedelta
from django.utils.translation import gettext_lazy as _

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
    # Baaki codes aap zarurat ke hisaab se add kar sakte hain
}

# Category section

class Category(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
            return self.name

    class Meta:
            verbose_name = "01. Category"
            verbose_name_plural = "01. Category"
    
# brand section


class Brand(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
            return self.name

    class Meta:
            verbose_name = "02. Brand"
            verbose_name_plural = "02. Brand"

# unit section


class Unit(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
            return self.name

    class Meta:
            verbose_name = "03. Unit"
            verbose_name_plural = "03. Unit"
                
    
# vendor section

class Supplier(models.Model):
    company = models.CharField(max_length=200,unique=True,blank=False, null=False,default="ABC Supplier")
    person = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=15,blank=True, null=True, verbose_name="Phone Number")
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=50, blank=True, null=True)
    state = models.CharField(max_length=50, editable=False, blank=True, null=True)
    gstin = models.CharField(max_length=15, unique=True, help_text="Enter 15 digit GSTIN",blank=True, null=True)
    pan = models.CharField(max_length=10, editable=False, blank=True, null=True)
    opening_balance = models.IntegerField(editable=False,blank=True,null=True,verbose_name="Balance")
    created_at = models.DateField(auto_now=True,blank=True, null=True)
    updated_at = models.DateField(auto_now=True,blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Active Status")


    def clean(self):
        if self.gstin == "":
            self.gstin = None
        # Validation: Ensure GSTIN is exactly 15 characters
        if self.gstin and len(self.gstin) != 15:
            raise ValidationError({'gstin': 'GSTIN 15 characters ka hona chahiye.'})

    def save(self, *args, **kwargs):
        # GSTIN se data extract karke fields mein save karna
        if self.gstin and len(self.gstin) == 15:
            self.gstin = self.gstin.upper() # Sabhi characters uppercase mein convert karna
            
            # PAN extraction (Index 2 se 12 tak)
            self.pan = self.gstin[2:12]
            
            # State extraction (Index 0 aur 1)
            state_code = self.gstin[0:2]
            self.state = GST_STATE_CODES.get(state_code, "Unknown State")
            
        # Asli save process ko call karna
        super().save(*args, **kwargs)

    
    
    def __str__(self):
        return self.company

    class Meta:
            verbose_name = "04. Supplier"
            verbose_name_plural = "04. Supplier"

# product section    
#         
def generate_unique_sku_id():
    # 4 character ka random string banayega (A-Z aur 0-9 mila kar)
    length = 4
    chars = string.ascii_uppercase + string.digits
    random_str = ''.join(random.choice(chars) for _ in range(length))
    return f"SKU-{random_str}"

class Product(models.Model):
    GST_CHOICES = (  
        (5.00, '5%'),
        (18.00, '18%'),
    )
    name = models.CharField(max_length=255,blank=True, null=True)
    hsn_code = models.CharField(max_length=10, blank=True, null=True,verbose_name="HSN / SAC")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    batch=models.CharField(editable=False,blank=True,null=True)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True)
    sku = models.CharField(max_length=50, unique=True,editable=False,blank=True, null=True)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, default=18.00,choices=GST_CHOICES,verbose_name="GST (%)")  # GST rate in percentage
    stock_qty = models.PositiveIntegerField(editable=False,default=0,verbose_name="Stock")
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    reorder_level = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True,blank=True, null=True)

    @property
    def is_low_stock(self):
        return self.stock_qty <= self.reorder_level
    
    def save(self, *args, **kwargs):
            # Jab naya order ban raha ho (order_id blank ho)
            if not self.sku:
                new_sku_id = generate_unique_sku_id()
                
                while Product.objects.filter(sku=new_sku_id).exists():
                    new_sku_id = generate_unique_sku_id()
                    
                self.sku = new_sku_id
                
            super().save(*args, **kwargs)
    

    def __str__(self):
        return f"{self.name} - Stock: {self.stock_qty}"

    class Meta:
            verbose_name = "05. Product"
            verbose_name_plural = "05. Product"

# product show section

class Productshow(Product):
        class Meta:
            proxy=True

        def __str__(self):
            return self.name       


# batch section

class Batch(models.Model):
    product=models.CharField(editable=False,blank=True,null=True)
    supplier=models.CharField(editable=False,blank=True,null=True)
    qty = models.IntegerField(editable=False,blank=True,null=True)
    unit=models.CharField(editable=False,blank=True,null=True)
    batch_number=models.CharField(editable=False,verbose_name="Batch")
    manufacture_date=models.DateField(editable=False,verbose_name="MFG Date")
    expire_date=models.DateField(editable=False,verbose_name="EXP Date")

    @property
    def expiry_status(self):
        if not self.expire_date:
            return "No Date"
            
        today = date.today()
        alert_date = today + timedelta(days=30) # 30 din ka alert period
        
        if self.expire_date < today:
            return "Expired"
        elif self.expire_date <= alert_date:
            return "Expiring Soon"
        else:
            return "Safe"


    def __str__(self):
            return self.batch_number 

    class Meta:
                verbose_name = "07. Batch"
                verbose_name_plural = "07. Batch"


# purchase section           
def generate_unique_order_id():
    # 6 character ka random string banayega (A-Z aur 0-9 mila kar)
    length = 6
    chars = string.ascii_uppercase + string.digits
    random_str = ''.join(random.choice(chars) for _ in range(length))
    return f"SOFY-{random_str}"

class Purchase(models.Model):
    product = models.ForeignKey(Productshow, on_delete=models.CASCADE,blank=True, null=True)
    batch=models.CharField(max_length=255,blank=True, null=True,editable=False)
    manufacture_date=models.DateField(blank=True, null=True,verbose_name="MFG Date")
    expire_date=models.DateField(blank=True, null=True,verbose_name="EXP Date")
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE,blank=True, null=True)
    qty = models.IntegerField()
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    purchase_price = models.IntegerField(blank=True, null=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0,verbose_name="Taxable Value")
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    gst_rate = models.IntegerField(editable=False, default=0,verbose_name="GST (%)") 
    order_date = models.DateField(auto_now_add=True)


    def save(self, *args, **kwargs):
        # Yeh check karta hai ki yeh nayi entry ban rahi hai ya purani edit ho rahi hai
        is_new = self.pk is None 
        amount_diff = Decimal('0.00')

        if not is_new:
            old_purchase = Purchase.objects.get(pk=self.pk)
            amount_diff = self.purchase_price - old_purchase.purchase_price
        else:
            amount_diff = self.purchase_price

        # Rate calculate
        
        if self.product:
            self.gst_rate = self.product.gst_rate
        purchase_price = self.purchase_price

        if self.qty > 0:
            self.tax = purchase_price / (self.gst_rate + 100) * 100
            self.rate = self.tax / self.qty
            self.gst = self.tax * (self.gst_rate/100)
            self.cgst = self.gst / 2
            self.sgst = self.gst / 2
        else:
            self.tax = Decimal('0.00')
            self.rate = Decimal('0.00')

        if not self.batch:
                    new_id = generate_unique_order_id()
                    while Order.objects.filter(order_id=new_id).exists():
                        new_id = generate_unique_order_id()
                        
                    self.batch = new_id

        is_new_batch = self.pk is None

        with transaction.atomic():
            if self.pk:
                # 1. EDIT CASE: Agar Purchase id already hai, matlab edit ho raha hai
                old_purchase = Purchase.objects.get(pk=self.pk)
                
                # Nayi quantity aur purani quantity ka difference nikalein
                difference = self.qty - old_purchase.qty 
                
                # Product ke stock mein difference add karein
                self.product.stock_qty += difference
            else:
                # 2. CREATE CASE: Nayi purchase ho rahi hai
                self.product.stock_qty += self.qty
                
            # Product ka stock database mein save karein
            self.product.batch = self.batch
            self.product.save()


        is_new_trans = self.pk is None   

        # Pehle Purchase entry ko save karte hain
        super().save(*args, **kwargs)

        if is_new_batch:
            Batch.objects.create(
                product=self.product,
                supplier=self.supplier,
                qty=self.qty,
                unit=self.unit,
                batch_number=self.batch,
                manufacture_date=self.manufacture_date,
                expire_date=self.expire_date
                        
            )

        if is_new_trans:
            Transaction.objects.create(
                purchase_date=self.order_date,
                supplier=self.supplier,
                purchase_amount=self.purchase_price,
                sale_date=None,
                customer=None,
                sale_amount=None,
                                
            )    

        if self.supplier and amount_diff != Decimal('0.00'):
            # Opening balance ko bhi Decimal me handle karein
            supplier_bal = Decimal(str(self.supplier.opening_balance or 0))
            self.supplier.opening_balance = supplier_bal + amount_diff
            self.supplier.save(update_fields=['opening_balance'])

    def delete(self, *args, **kwargs):
            with transaction.atomic():
                if self.product: 
                    self.product.stock_qty -= self.qty
                    self.product.save()

                if self.supplier and self.purchase_price:
                    # Purana balance lijiye (agar None hai toh 0 set karein)
                    supplier_bal = Decimal(str(self.supplier.opening_balance or 0))
                    purchase_amt = Decimal(str(self.purchase_price or 0))
                
                    # Balance minus karein
                    self.supplier.opening_balance = supplier_bal - purchase_amt
                
                    # Sirf opening_balance field ko database mein update karein
                    self.supplier.save(update_fields=['opening_balance'])

                if self.batch:
                # Is purchase ke batch number wala Batch dhund kar delete karega
                    Batch.objects.filter(batch_number=self.batch).delete()  

            super().delete(*args, **kwargs)


    def __str__(self):
        return f"Purchase: {self.product.name} - Qty: {self.qty}"
    
    class Meta:
            verbose_name = "06. Purchase "
            verbose_name_plural = "06. Purchase "

# sales section

class Sale(models.Model):
    date=models.DateField(auto_now_add=True,null=True)
    name = models.CharField(editable=False,null=True)
    invoice_mode = models.CharField(editable=False)
    taxable_value = models.IntegerField(blank=True, editable=False,null=True)
    gst=models.IntegerField(blank=True, editable=False,null=True)
    total = models.IntegerField(editable=False)
    invoice_item = models.OneToOneField('InvoiceItem', on_delete=models.CASCADE, null=True, blank=True)
    
    def save(self, *args, **kwargs):
        
        is_new_trans = self.pk is None

        with transaction.atomic():
        # Pehle parent object ko save karein
            super().save(*args, **kwargs)

        if is_new_trans:
            Transaction.objects.create(
                purchase_date=None,
                supplier=None,
                purchase_amount=None,
                sale_date=self.date,
                customer=self.name,
                sale_amount=self.total,
                                        
            )

    def __str__(self):
        return self.name 

    class Meta:
            verbose_name = "10. Sales"
            verbose_name_plural = "10. Sales"

# customer order section
#  
def generate_unique_order_id():
    # 6 character ka random string banayega (A-Z aur 0-9 mila kar)
    length = 6
    chars = string.ascii_uppercase + string.digits
    random_str = ''.join(random.choice(chars) for _ in range(length))
    return f"SOFY-{random_str}"

class Order(models.Model):
    order_id = models.CharField(max_length=20, unique=True,null=True,editable=False,blank=True)
    customers_name = models.CharField(max_length=100,blank=True, null=True)
    customer_product = models.ForeignKey(Productshow, on_delete=models.CASCADE,blank=True, null=True)
    customer_rate = models.IntegerField(blank=True, null=True)
    customer_qty = models.IntegerField(blank=True, null=True)
    order_date = models.DateField(blank=True, null=True)

    def save(self, *args, **kwargs):
        # Jab naya order ban raha ho (order_id blank ho)
        if not self.order_id:
            new_id = generate_unique_order_id()
            
            # Check karein ki yeh ID pehle se kisi aur order ko toh nahi mili
            # Agar mili hai toh loop chalega jab tak unique ID na mil jaye
            while Order.objects.filter(order_id=new_id).exists():
                new_id = generate_unique_order_id()
                
            self.order_id = new_id
            
        super().save(*args, **kwargs)

    class Meta:
                verbose_name = "08. Order "
                verbose_name_plural = "08. Orders"

    def __str__(self):
            return f"{self.order_id} "  

# bill section 

class Bill(models.Model):  
    MODE_CHOICES = (
        ('online', 'Online'),
        ('offline', 'Offline'),
    )                      
    customer_name = models.CharField(max_length=255, default="Cash")
    date = models.DateField(blank=True, null=True)
    invoice_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='offline')
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # 1. Customer Name Auto-fill: Agar cash likha hai, toh Order wale customer ka naam dal do
        if self.invoice_mode == 'online' and self.order:
            if self.customer_name == "Cash" and self.order.customers_name:
                self.customer_name = self.order.customers_name

            if not self.date and self.order.order_date:
                self.date = self.order.order_date    

        # Pehle Bill ko save karna zaroori hai, taaki iski ID generate ho jaye
        super(Bill, self).save(*args, **kwargs)

        # 2. InvoiceItem Auto-create Logic
        if self.invoice_mode == 'online' and self.order:
            # Check karein ki is bill me pehle se items toh nahi hain 
            # (Taaki edit karne par duplicate items na bane)
            if not self.items.exists():
                # Yahan hum InvoiceItem model ko import kar rahe hain (circular import se bachne ke liye)
                from .models import InvoiceItem 
                
                # Order ka data use karke InvoiceItem auto-create kar rahe hain
                InvoiceItem.objects.create(
                    bill=self,
                    product=self.order.customer_product,
                    rate=self.order.customer_rate,
                    qty=self.order.customer_qty,

                )
                # Note: InvoiceItem.objects.create() chalne par InvoiceItem ka apna save() 
                # function chalega aur saari calculation (GST, Total, Stock) khud ba khud ho jayegi.

    def delete(self, *args, **kwargs):
        # Bill delete hone se pehle, uske andar ke har item ko manual delete karein
        # Taaki InvoiceItem ka apna delete() function properly run ho aur stock wapas jaye
        with transaction.atomic():
            # 'items' wahi related_name hai jo aapne InvoiceItem me bill field me diya tha
            for item in self.items.all():
                item.delete() 
                
        # Phir bill ko delete kar dein
        super(Bill, self).delete(*args, **kwargs)

    def __str__(self):
            return f"Bill #{self.id} - {self.customer_name}"

    @property
    def total(self):
        # 'items' related_name hai jo aapne InvoiceItem mein define kiya tha
        total = self.items.aggregate(total_sum=Sum('total'))['total_sum']
        return round(total, 2) if total else 0

    @property
    def total_items(self):
        return self.items.count()

    @property
    def taxable(self):
        total = self.items.aggregate(total_sum=Sum('taxable_value'))['total_sum']
        return round(total, 2) if total else 0.00

    @property
    def cgst(self):
        total = self.items.aggregate(total_sum=Sum('cgst'))['total_sum']
        return round(total, 2) if total else 0.00

    @property
    def sgst(self):
        total = self.items.aggregate(total_sum=Sum('sgst'))['total_sum']
        return round(total, 2) if total else 0.00

    @property
    def igst(self):
        total = self.items.aggregate(total_sum=Sum('igst'))['total_sum']
        return round(total, 2) if total else 0.00

    class Meta:
            verbose_name = "09. Bills"
            verbose_name_plural = "09. Bills"

# InvoiceItem section

class InvoiceItem(models.Model):
    bill = models.ForeignKey(Bill, related_name='items', on_delete=models.CASCADE,blank=True, null=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, blank=True, null=True)
    batch_history = models.JSONField(default=dict, blank=True, null=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2,blank=True, null=True)
    qty = models.IntegerField( blank=True, null=True)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True, verbose_name="Discount (%)")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Discount (Flat)")
    gst_rate = models.IntegerField(default=0, editable=False)
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2,editable=False, default=0)
    igst = models.DecimalField(max_digits=10, decimal_places=2,editable=False, default=0)
    is_igst = models.BooleanField(default=False, verbose_name="Apply IGST")
    total = models.IntegerField(editable=False,blank=True, null=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        

        if self.bill and self.bill.invoice_mode == 'online' and self.bill.order:
            # Pura data order se uthayenge
            self.product = self.bill.order.customer_product
            self.rate = self.bill.order.customer_rate
            self.qty = self.bill.order.customer_qty 
             
            # Agar order ka customer name bill me dalna hai, toh aap ye kar sakte hain:
            if self.bill.customer_name == "Cash": # Agar default hai
                self.bill.customer_name = self.bill.order.customers_name
                self.bill.save() # Bill ko update kar denge 

        if self.product:
            self.gst_rate = self.product.gst_rate

            rate_val = Decimal(str(self.rate or 0))
            qty_val = Decimal(str(self.qty or 0))

            gross_amount = rate_val * qty_val

            disc_percent_val = Decimal(str(self.discount_percent or 0))
            disc_flat_val = Decimal(str(self.discount_amount or 0))

            calculated_percent_discount = gross_amount * (disc_percent_val / Decimal('100'))
            total_discount = calculated_percent_discount + disc_flat_val

            #Taxable Value (Gross Amount - Discount)
            self.taxable_value = gross_amount - total_discount

            if self.taxable_value < Decimal('0'):
                self.taxable_value = Decimal('0')

            gst_rate_dec = Decimal(str(self.gst_rate or 0))
            self.gst = self.taxable_value * (gst_rate_dec / Decimal('100'))  

            if self.is_igst:
                self.cgst = Decimal('0')
                self.sgst = Decimal('0')
                self.igst = self.gst
            else:
            # Agar IGST checked nahi hai, toh IGST 0 hoga
                self.igst = Decimal('0')
                self.cgst = self.gst / Decimal('2')
                self.sgst = self.gst / Decimal('2')

            self.total = int(self.taxable_value + self.gst)

        is_new_invoice = self.pk is None

        with transaction.atomic():
            if self.pk:
                # 1. EDIT CASE: Agar Purchase id already hai, matlab edit ho raha hai
                old_purchase = InvoiceItem.objects.get(pk=self.pk)
                        
                # Nayi quantity aur purani quantity ka difference nikalein
                difference = self.qty - old_purchase.qty 
                        
                # Product ke stock mein difference add karein
                self.product.stock_qty -= difference
                # Batch update logic
                if difference > 0:
                    self._deduct_from_batches(difference)
                elif difference < 0:
                    self._add_to_batches(abs(difference))
            else:
                # 2. CREATE CASE: Nayi purchase ho rahi hai
                self.product.stock_qty -= self.qty
                self._deduct_from_batches(self.qty)
                        
            # Product ka stock database mein save karein
            self.product.save()

        super(InvoiceItem, self).save(*args, **kwargs)

        if is_new_invoice:
            Sale.objects.create(
                invoice_item=self,
                name=self.bill.customer_name,
                invoice_mode=self.bill.invoice_mode,
                taxable_value=self.taxable_value,
                gst=self.gst,
                total=self.total
            )


    def delete(self, *args, **kwargs):
            with transaction.atomic():
                if self.product and self.qty: 
                    self.product.stock_qty += self.qty
                    self.product.save()
                    self._restore_to_exact_batches()

                else:
                    print("ERROR: Product ya Qty missing hai, isliye update nahi hua!")    
            super().delete(*args, **kwargs)
            print("--- INVOICE ITEM DELETED SUCCESSFULLY ---\n")

    # --- FEFO LOGIC FUNCTIONS ---

    def _deduct_from_batches(self, required_qty):
        """ FEFO ke according batches se quantity minus karega """
        # NOTE: Agar Batch model me product ek string (CharField) hai, 
        # toh yahan self.product.name ya jo bhi string match kare wo likhein.
        product_identifier = str(self.product.name) # Ya self.product.name (depends on how you save it in Batch)
        
        # Order by 'expire_date' (Jo pehle expire hoga, wo pehle aayega)
        batches = Batch.objects.filter(
            product=product_identifier, 
            qty__gt=0
        ).order_by('expire_date')

        remaining_qty = required_qty
        history = self.batch_history or {}

        for batch in batches:
            if remaining_qty <= 0:
                break

            batch_id_str = str(batch.id)
                
            if batch.qty >= remaining_qty:
                batch.qty -= remaining_qty
                history[batch_id_str] = history.get(batch_id_str, 0) + remaining_qty
                batch.save()
                remaining_qty = 0
            else:
                history[batch_id_str] = history.get(batch_id_str, 0) + batch.qty
                remaining_qty -= batch.qty
                batch.qty = 0
                batch.save()

        # Agar saare batches check karne ke baad bhi qty bach jaye
        if remaining_qty > 0:
            raise ValidationError(f"Stock me itni quantity (Batches me) available nahi hai. Short by: {remaining_qty}")
        self.batch_history = history

    def _restore_to_exact_batches(self):
        """ Delete/Edit hone par record padhkar wapas USI batch mein daalega """
        history = self.batch_history or {}
        
        if history:
            for batch_id_str, deducted_qty in history.items():
                try:
                    batch = Batch.objects.get(id=int(batch_id_str))
                    batch.qty += deducted_qty
                    batch.save()
                    print(f"RESTORED: {deducted_qty} qty added back to Batch ID {batch.id}")
                except Batch.DoesNotExist:
                    print(f"WARNING: Batch ID {batch_id_str} ab database mein exist nahi karta!")
            
            # Restore ke baad history clear
            self.batch_history = {}
            
        # AGAR HISTORY KHALI HAI (Purane Invoices ke liye jisme record nahi tha)
        else:
            print("No batch history! Running fallback logic...")
            product_identifier = str(self.product.name) # ya id, jo aap pehle use kar rahe the
            
            # Latest expire hone wale batch mein wapas daal do (Fallback)
            batch = Batch.objects.filter(product=product_identifier).order_by('-expire_date').first()
            if batch:
                batch.qty += self.qty
                batch.save()
                print(f"FALLBACK RESTORED: {self.qty} qty added to Batch {batch.batch_number}")
            else:
                print("ERROR: Koi purana batch nahi mila fallback ke liye!")        

    def __str__(self):
            return f"{self.product} x {self.qty}"


# payment section

class Payment(models.Model):
    TRANSACTION_TYPES = (
        ('CASH', 'Cash'),
        ('BANK', 'Bank'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='payments')
    amount = models.IntegerField()
    due=models.IntegerField(editable=False,blank=True,null=True)
    payment_mode = models.CharField(max_length=50, choices=TRANSACTION_TYPES,blank=True, null=True)
    payment_date = models.DateField(auto_now_add=True)


    def save(self, *args, **kwargs):
           
        with transaction.atomic():
            is_new = self.pk is None
            amount_diff = 0

            # Step 1: Naya amount aur purane amount ke beech ka difference nikalna
            if not is_new:
                # Agar payment edit ho raha hai, toh purana payment data nikalein
                old_payment = Payment.objects.get(pk=self.pk)
                # Naye amount aur purane amount ka difference.
                # Example: Pehle 100 tha, ab 150 kiya, toh difference +50 hoga (supplier se 50 aur minus hoga)
                amount_diff = self.amount - old_payment.amount
            else:
                # Nayi payment hai toh difference pura amount hi hoga
                amount_diff = self.amount

            # Step 2: Supplier ko database mein lock karke fetch karna taaki koi aur request isey ek sath update na kar de
            supplier = Supplier.objects.select_for_update().get(pk=self.supplier.pk)
            
            # Agar supplier ka opening balance None hai (empty hai), toh usko 0 maan lein
            current_balance = supplier.opening_balance if supplier.opening_balance is not None else 0

            # Step 3: Naya due balance calculate karein (Purana balance - jo difference aya hai)
            new_due_balance = current_balance - amount_diff

            # Step 4: Payment ke 'due' field mein current remaining balance update karein
            self.due = new_due_balance

            # Step 5: Supplier ke model mein naya balance save karein
            supplier.opening_balance = new_due_balance
            supplier.save(update_fields=['opening_balance'])
            
        super().save(*args, **kwargs)
            
            
    def delete(self, *args, **kwargs):
        # Jab payment delete ho, toh supplier ka balance wapas add hona chahiye
        with transaction.atomic():
            # Supplier ko fetch karein
            supplier = Supplier.objects.select_for_update().get(pk=self.supplier.pk)
            
            current_balance = supplier.opening_balance if supplier.opening_balance is not None else 0
            
            # Jo amount delete ho raha hai, usko wapas supplier ke balance mein add kar dein
            supplier.opening_balance = current_balance + self.amount
            supplier.save(update_fields=['opening_balance'])

            # Payment ko delete kar dein
            super().delete(*args, **kwargs)
            
    class Meta:
                verbose_name = "11. Payment"
                verbose_name_plural = "11. Payment"


# transaction section

class Transaction(models.Model):
    purchase_date = models.DateField(editable=False,blank=True,null=True)
    supplier = models.CharField(max_length=255,editable=False,blank=True,null=True)
    purchase_amount=models.IntegerField(editable=False,blank=True,null=True)
    sale_date = models.DateField(editable=False,blank=True,null=True)
    customer = models.CharField(max_length=255,editable=False,blank=True,null=True)
    sale_amount=models.IntegerField(editable=False,blank=True,null=True)


    class Meta:
            verbose_name = "13. Transaction"
            verbose_name_plural = "13. Transaction"
    

                                  