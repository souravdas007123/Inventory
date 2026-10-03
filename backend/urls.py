from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='app/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('report/', views.report, name='report'),
    path('<str:key>/', views.listing, name='list'),
    path('<str:key>/new/', views.edit, name='new'),
    path('<str:key>/<int:pk>/', views.edit, name='edit'),
    path('<str:key>/<int:pk>/delete/', views.delete, name='delete'),
]