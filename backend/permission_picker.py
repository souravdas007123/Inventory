"""The permission lists in a shop's admin panel (Users and Groups).

The shop owner gives a staff member permissions from a list. That list must hold only what a shop uses. The website pages of the main
site (blog, questions, legal pages, home text, social links) and the plumbing of Django itself (log entries, sessions, content types,
permissions) do not belong in a shop, so they are left out of the list there. On the main site nothing changes.
Only the choices are filtered. What a person is allowed to do is decided exactly as before.
"""
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, Permission, User
from django.db.models import Q

from . import tenancy

# (app label, model) pairs that a shop never needs to hand out
HIDDEN_IN_SHOPS = [
    ('backend', 'blogpost'), ('backend', 'faqitem'), ('backend', 'legalpage'), ('backend', 'hometext'), ('backend', 'sociallink'),
    ('backend', 'doccounter'), ('backend', 'invoicecounter'), ('backend', 'healthsnapshot'),
    ('admin', 'logentry'), ('auth', 'permission'), ('contenttypes', 'contenttype'), ('sessions', 'session'),
]


def shop_permissions(queryset):
    """The permissions a shop may hand out: the given ones without the models above."""
    hidden = Q()
    for app, model in HIDDEN_IN_SHOPS:
        hidden |= Q(content_type__app_label=app, content_type__model=model)
    return queryset.exclude(hidden)


class _ShopPermissionList:
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == 'permissions' or db_field.name == 'user_permissions':
            if not tenancy.is_platform():
                kwargs['queryset'] = shop_permissions(Permission.objects.select_related('content_type')).order_by(
                    'content_type__app_label', 'content_type__model', 'codename')
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class ShopUserAdmin(_ShopPermissionList, UserAdmin):
    pass


class ShopGroupAdmin(_ShopPermissionList, GroupAdmin):
    pass


admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.register(User, ShopUserAdmin)
admin.site.register(Group, ShopGroupAdmin)
