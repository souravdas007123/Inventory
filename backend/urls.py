from django.contrib.auth import views as auth_views
from django.urls import path

from . import security, views, views_extra, views_import

# NOTE: specific paths must always come BEFORE the generic '<str:key>/...' patterns below.
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', security.ThrottledLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('report/', views.report, name='report'),
    path('shop/', views.shop, name='shop'),
    path('bills/<int:pk>/invoice/', views.invoice, name='invoice'),
    path('bills/<int:pk>/cancel/', views.bill_cancel, name='bill_cancel'),

    # Customer ledger / returns / write-off
    path('customers/<int:pk>/ledger/', views_extra.customer_ledger, name='customer_ledger'),
    path('bills/<int:pk>/return/', views_extra.sales_return_new, name='sales_return_new'),
    path('purchases/<int:pk>/return/', views_extra.purchase_return_new, name='purchase_return_new'),
    path('sales_returns/<int:pk>/note/', views_extra.sales_return_note, name='sales_return_note'),
    path('purchase_returns/<int:pk>/note/', views_extra.purchase_return_note, name='purchase_return_note'),
    path('batches/<int:pk>/writeoff/', views_extra.batch_writeoff, name='batch_writeoff'),

    # Reports
    path('gst/', views_extra.gst_view, name='gst'),
    path('drug_register/', views_extra.drug_register_view, name='drug_register'),
    path('api/customer_lookup/', views_extra.customer_lookup, name='customer_lookup'),
    path('api/product_find/', views_extra.product_find, name='product_find'),
    path('api/product_suggest/', views_extra.product_suggest, name='product_suggest'),
    path('api/product_quick_add/', views_extra.product_quick_add, name='product_quick_add'),
    path('api/last_purchase/', views_extra.last_purchase, name='last_purchase'),
    path('api/supplier_price_hint/', views_extra.supplier_price_hint, name='supplier_price_hint'),
    path('reorder/', views_extra.reorder_view, name='reorder'),
    path('refill/', views_extra.refill_view, name='refill'),
    path('salary/', views_extra.salary_view, name='salary'),
    path('supplier_prices/', views_extra.supplier_prices_view, name='supplier_prices'),
    path('supplier_prices/<int:pk>/', views_extra.supplier_prices_product, name='supplier_prices_product'),

    # Billing screen helper (barcode / quick search)
    path('api/products/lookup/', views_extra.product_lookup, name='product_lookup'),

    # Alerts + Purchase Order share
    path('alerts/send/', views_extra.alerts_send, name='alerts_send'),
    path('alerts/whatsapp/', views_extra.alerts_whatsapp, name='alerts_whatsapp'),
    path('summary/whatsapp/', views_extra.summary_whatsapp, name='summary_whatsapp'),
    path('purchase_orders/<int:pk>/print/', views_extra.po_print, name='po_print'),
    path('purchase_orders/<int:pk>/email/', views_extra.po_email, name='po_email'),
    path('purchase_orders/<int:pk>/whatsapp/', views_extra.po_whatsapp, name='po_whatsapp'),

    # Physical stock audit
    path('stock_audits/start/', views_extra.audit_start, name='audit_start'),
    path('stock_audits/<int:pk>/count/', views_extra.audit_count, name='audit_count'),

    # Backup
    path('backup/', views_extra.backup_page, name='backup'),
    path('backup/download/<str:name>/', views_extra.backup_download, name='backup_download'),

    # Bulk import / export
    path('import/', views_import.import_home, name='import_home'),
    path('import/<str:kind>/', views_import.import_view, name='import'),

    # Generic list / new / edit / delete (CONFIG based)
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/new/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/delete/', views.delete, name='delete'),
]
