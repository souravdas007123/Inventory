from django.urls import path

from . import views

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/confirm/<str:token>/', views.confirm, name='signup_confirm'),
]
