from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_extra

# NOTE: specific paths hamesha neeche wale generic '<str:key>/...' patterns se PEHLE aane chahiye.
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='app/login.html'), name='login'),
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

    # Billing screen helper (barcode / quick search)
    path('api/products/lookup/', views_extra.product_lookup, name='product_lookup'),

    # Alerts + Purchase Order share
    path('alerts/send/', views_extra.alerts_send, name='alerts_send'),
    path('purchase_orders/<int:pk>/print/', views_extra.po_print, name='po_print'),
    path('purchase_orders/<int:pk>/email/', views_extra.po_email, name='po_email'),
    path('purchase_orders/<int:pk>/whatsapp/', views_extra.po_whatsapp, name='po_whatsapp'),

    # Physical stock audit
    path('stock_audits/start/', views_extra.audit_start, name='audit_start'),
    path('stock_audits/<int:pk>/count/', views_extra.audit_count, name='audit_count'),

    # Generic list / new / edit / delete (CONFIG based)
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/new/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/delete/', views.delete, name='delete'),
]
