from django.urls import path

from . import mainlogin, plan, platform, views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/confirm/<str:token>/', views.confirm, name='signup_confirm'),
    path('sso/<str:token>/', mainlogin.sso, name='sso'),
    path('plan/', plan.plan, name='plan'),
    path('superadmin/', platform.dashboard, name='platform'),
    path('superadmin/site/', platform.site_settings, name='platform_site'),
    path('superadmin/shop/<int:pk>/', platform.shop_detail, name='platform_shop'),
    path('superadmin/shop/<int:pk>/delete/', platform.shop_delete, name='platform_shop_delete'),
    path('superadmin/shop/<int:pk>/support/', platform.shop_support, name='platform_shop_support'),
    path('superadmin/shop/<int:pk>/action/', platform.shop_action, name='platform_shop_action'),
]
