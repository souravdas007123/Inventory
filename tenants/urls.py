from django.urls import path

from . import google, mainlogin, maintenance_admin, payadmin, payshop, plan, platform, server_admin, support, supportadmin, views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/confirm/<str:token>/', views.confirm, name='signup_confirm'),
    path('auth/google/', google.start, name='google_start'),
    path('auth/google/callback/', google.callback, name='google_callback'),
    path('sso/<str:token>/', mainlogin.sso, name='sso'),
    path('plan/', plan.plan, name='plan'),
    path('plan/pay/autopay/', payshop.autopay_start, name='plan_autopay_start'),
    path('plan/pay/autopay/verify/', payshop.autopay_verify, name='plan_autopay_verify'),
    path('plan/pay/autopay/<int:pk>/cancel/', payshop.autopay_cancel, name='plan_autopay_cancel'),
    path('plan/pay/upi/', payshop.upi_start, name='plan_upi_start'),
    path('plan/pay/upi/<int:pk>/', payshop.upi_page, name='plan_upi'),
    path('plan/pay/<int:pk>/cancel/', payshop.order_cancel, name='plan_order_cancel'),
    path('razorpay/webhook/', payadmin.razorpay_webhook, name='razorpay_webhook'),
    path('superadmin/payments/', payadmin.payments, name='platform_payments'),
    path('superadmin/payments/<int:pk>/approve/', payadmin.approve, name='platform_payment_approve'),
    path('superadmin/payments/<int:pk>/reject/', payadmin.reject, name='platform_payment_reject'),
    path('superadmin/', platform.dashboard, name='platform'),
    path('support/', support.ticket_list, name='support'),
    path('support/<int:pk>/', support.ticket_detail, name='support_ticket'),
    path('superadmin/support/', supportadmin.ticket_list, name='platform_support'),
    path('superadmin/support/<int:pk>/', supportadmin.ticket_detail, name='platform_ticket'),
    path('superadmin/site/', platform.site_settings, name='platform_site'),
    path('superadmin/server/', server_admin.server_page, name='platform_server'),
    path('superadmin/maintenance/', maintenance_admin.maintenance_page, name='platform_maintenance'),
    path('superadmin/shop/<int:pk>/', platform.shop_detail, name='platform_shop'),
    path('superadmin/shop/<int:pk>/delete/', platform.shop_delete, name='platform_shop_delete'),
    path('superadmin/shop/<int:pk>/two-step-off/', platform.shop_two_step_off, name='platform_shop_two_step_off'),
    path('superadmin/shop/<int:pk>/action/', platform.shop_action, name='platform_shop_action'),
]
