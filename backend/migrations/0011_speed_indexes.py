from django.db import migrations, models


class Migration(migrations.Migration):
    """Indexes for the screens that filter / group by product (low stock, reorder, FEFO, price history). No data changes."""

    dependencies = [
        ('backend', '0010_import_layout'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['is_active', 'stock_qty'], name='product_active_stock_idx'),
        ),
        migrations.AddIndex(
            model_name='batch',
            index=models.Index(fields=['product', 'expire_date'], name='batch_prod_exp_idx'),
        ),
        migrations.AddIndex(
            model_name='invoiceitem',
            index=models.Index(fields=['product', 'bill'], name='invitem_product_bill_idx'),
        ),
        migrations.AddIndex(
            model_name='purchaseitem',
            index=models.Index(fields=['product', 'purchase'], name='pitem_product_purchase_idx'),
        ),
    ]
