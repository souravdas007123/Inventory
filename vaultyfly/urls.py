from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
]
if settings.MULTI_SHOP:
    urlpatterns.append(path('', include('tenants.urls')))      # before the app's urls: they end with a catch-all '<key>/'
urlpatterns.append(path('', include('backend.urls')))
