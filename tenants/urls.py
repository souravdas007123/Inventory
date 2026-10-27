from django.urls import path

from . import mainlogin, maintenance_admin, plan, platform, support, supportadmin, views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/confirm/<str:token>/', views.confirm, name='signup_confirm'),
    path('sso/<str:token>/', mainlogin.sso, name='sso'),
    path('plan/', plan.plan, name='plan'),
    path('superadmin/', platform.dashboard, name='platform'),
    path('support/', support.ticket_list, name='support'),
    path('support/<int:pk>/', support.ticket_detail, name='support_ticket'),
    path('superadmin/support/', supportadmin.ticket_list, name='platform_support'),
    path('superadmin/support/<int:pk>/', supportadmin.ticket_detail, name='platform_ticket'),
    path('superadmin/site/', platform.site_settings, name='platform_site'),
    path('superadmin/maintenance/', maintenance_admin.maintenance_page, name='platform_maintenance'),
    path('superadmin/shop/<int:pk>/', platform.shop_detail, name='platform_shop'),
    path('superadmin/shop/<int:pk>/delete/', platform.shop_delete, name='platform_shop_delete'),
    path('superadmin/shop/<int:pk>/two-step-off/', platform.shop_two_step_off, name='platform_shop_two_step_off'),
    path('superadmin/shop/<int:pk>/action/', platform.shop_action, name='platform_shop_action'),
]
