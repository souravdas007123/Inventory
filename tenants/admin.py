"""Admin pages for the shops. Only the platform (the main site, schema "public") may see them: the owner of an
ordinary shop is a superuser inside his own shop and must never see other shops here."""
from django.contrib import admin
from django.db import connection

from .models import Domain, PendingSignup, Shop


def on_platform():
    return getattr(connection, 'schema_name', 'public') == 'public'


class PlatformOnly:
    def has_module_permission(self, request):
        return on_platform() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return on_platform() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return on_platform() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return on_platform() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False                      # a shop is paused, never deleted from here (its data would stay behind)


class DomainInline(admin.TabularInline):
    model = Domain
    extra = 0


@admin.register(Shop)
class ShopAdmin(PlatformOnly, admin.ModelAdmin):
    list_display = ('name', 'schema_name', 'owner_name', 'email', 'plan', 'trial_ends', 'paid_until', 'is_paused', 'created_on')
    list_filter = ('plan', 'is_paused', 'email_verified')
    search_fields = ('name', 'owner_name', 'email', 'phone', 'schema_name')
    readonly_fields = ('schema_name', 'created_on')
    inlines = [DomainInline]


@admin.register(Domain)
class DomainAdmin(PlatformOnly, admin.ModelAdmin):
    list_display = ('domain', 'tenant', 'is_primary')
    search_fields = ('domain',)


@admin.register(PendingSignup)
class PendingSignupAdmin(PlatformOnly, admin.ModelAdmin):
    list_display = ('shop_name', 'subdomain', 'email', 'phone', 'created_at', 'used')
    list_filter = ('used',)
    search_fields = ('shop_name', 'email', 'subdomain')
    exclude = ('password_hash', 'token')
