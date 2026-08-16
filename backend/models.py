from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.db.models.aggregates import Sum
from django.utils import timezone
from django.core.exceptions import ValidationError
import random
import string
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
    
# Category section


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
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00,editable=False)
    created_at = models.DateField(auto_now=True,blank=True, null=True)
    updated_at = models.DateField(auto_now=True,blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Active Status")


    def clean(self):
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
    name = models.CharField(max_length=255)
    hsn_code = models.CharField(max_length=10, blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True)
    sku = models.CharField(max_length=50, unique=True,editable=False,blank=True, null=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2,blank=True, null=True)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, default=18.00,choices=GST_CHOICES)  # GST rate in percentage
    # Ye threshold "Low stock Reorder Retail purchase" loop ko trigger karega
    stock_qty = models.PositiveIntegerField(editable=False,default=0)
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
        
# godown section

class Godown(models.Model):
    name = models.CharField(max_length=255) # e.g., "Stock/Godown 1"
    location = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
            verbose_name = "06. Godown"
            verbose_name_plural = "06. Godown"   

# purchase section           

class Purchase(models.Model):
    product = models.ForeignKey(Productshow, on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE,blank=True, null=True)
    godown = models.ForeignKey(Godown, on_delete=models.CASCADE)
    qty = models.IntegerField()
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    purchase_price = models.IntegerField(default=0.00,blank=True, null=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    gst_rate = models.IntegerField(editable=False, default=0)  # GST will be calculated as rate * qty
      # GST will be calculated as rate * qty
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

        
        # Pehle Purchase entry ko save karte hain
        super().save(*args, **kwargs)
        
        # Agar nayi entry hai, toh Product ka stock add (+) kar do
        if is_new:
            self.product.stock_qty += self.qty
            self.product.save()

        if self.supplier and amount_diff != Decimal('0.00'):
            # Opening balance ko bhi Decimal me handle karein
            supplier_bal = Decimal(str(self.supplier.opening_balance or 0))
            self.supplier.opening_balance = supplier_bal + amount_diff
            self.supplier.save(update_fields=['opening_balance'])

    def __str__(self):
        return f"Purchase: {self.product.name} - Qty: {self.qty}"
    
    class Meta:
            verbose_name = "07. Purchase "
            verbose_name_plural = "07. Purchase "

# sales section

class Sale(models.Model):
    product = models.CharField(editable=False)
    qty = models.IntegerField(editable=False)
    unit = models.CharField(blank=True, editable=False)
    sale_price = models.IntegerField(editable=False)
    

    def __str__(self):
        return self.product 

    class Meta:
            verbose_name = "08. Sales"
            verbose_name_plural = "08. Sales"

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
    customer_rate = models.DecimalField(max_digits=10, decimal_places=2,blank=True, null=True)
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
                verbose_name = "09. Order "
                verbose_name_plural = "09. Orders"

    def __str__(self):
            return f"{self.order_id} "            

# InvoiceItem section

class InvoiceItem(models.Model):
    MODE_CHOICES = (
        ('online', 'Online'),
        ('offline', 'Offline'),
    )
    invoice_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='offline')
    date = models.DateField(auto_now_add=True,blank=True, null=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=255, default="Cash")
    product = models.ForeignKey(Productshow, on_delete=models.CASCADE, blank=True, null=True)
    gst_rate = models.IntegerField(default=0, editable=False)
    rate = models.DecimalField(max_digits=10, decimal_places=2,default=0, blank=True, null=True)
    qty = models.IntegerField( blank=True, null=True)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True)
    gst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    cgst = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    sgst = models.DecimalField(max_digits=10, decimal_places=2,editable=False, default=0)
    igst = models.DecimalField(max_digits=10, decimal_places=2,editable=False, default=0)
    is_igst = models.BooleanField(default=False, verbose_name="Apply IGST")
    total = models.IntegerField(editable=False,blank=True, null=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        

        if self.invoice_mode == 'online' and self.order:
            # Customer Order se data auto-fill karein
            self.name = self.order.customers_name
            self.product = self.order.customer_product
            self.rate = self.order.customer_rate
            self.qty = self.order.customer_qty 

        if self.product:
            self.gst_rate = self.product.gst_rate

            rate_val = self.rate or 0
            qty_val = self.qty or 0

            self.taxable_value = rate_val * qty_val
            self.gst=self.taxable_value * self.gst_rate / 100

            if self.is_igst:
                self.cgst = 0
                self.sgst = 0
                self.igst = self.gst
            else:
            # Agar IGST checked nahi hai, toh IGST 0 hoga
                self.igst =0
                self.cgst = self.gst / 2
                self.sgst = self.gst / 2   
            self.total = self.taxable_value + (self.taxable_value * self.gst_rate / 100)

        is_new_invoice = self.pk is None

        super(InvoiceItem, self).save(*args, **kwargs)

        if is_new_invoice:
            Sale.objects.create(
                product=self.product,
                sale_price=self.total,
                qty=self.qty,
                unit=self.unit
            )

        if is_new:
            self.product.stock_qty -= self.qty
            self.product.save()

    class Meta:
                verbose_name = "10. Create Invoice"
                verbose_name_plural = "10. Create Invoice"

# invoice items create section



# payment section

class Payment(models.Model):
    TRANSACTION_TYPES = (
        ('CASH', 'Cash'),
        ('BANK', 'Bank'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=50, choices=TRANSACTION_TYPES,blank=True, null=True)
    payment_date = models.DateField(auto_now_add=True)


    def save(self, *args, **kwargs):
            # Yeh check karta hai ki yeh nayi entry ban rahi hai ya purani edit ho rahi hai
            is_new = self.pk is None 
    
            amount_diff = 0
            if not is_new:
                old_payment = Payment.objects.get(pk=self.pk)
                amount_diff = float(self.amount) - float(old_payment.amount)
            else:
                amount_diff = float(self.amount)
                
            
            # Pehle Purchase entry ko save karte hain
            super().save(*args, **kwargs)
            
            # Agar nayi entry hai, toh Product ka stock add (+) kar do
            if self.supplier and amount_diff != 0:
                self.supplier.opening_balance = float(self.supplier.opening_balance) - amount_diff
                self.supplier.save(update_fields=['opening_balance'])
    
    class Meta:
                verbose_name = "11. Payment"
                verbose_name_plural = "11. Payment"

  

                                  