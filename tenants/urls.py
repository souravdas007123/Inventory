from django.urls import path

from . import platform, views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/confirm/<str:token>/', views.confirm, name='signup_confirm'),
    path('superadmin/', platform.dashboard, name='platform'),
    path('superadmin/shop/<int:pk>/', platform.shop_detail, name='platform_shop'),
    path('superadmin/shop/<int:pk>/action/', platform.shop_action, name='platform_shop_action'),
]
