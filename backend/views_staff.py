"""Staff and Access pages: add a staff member, choose a role, tick what they may do, disable or enable them.

Owner only. A staff member made here is never a Django-admin user (is_staff stays off) and never a superuser, and the ticks can
only stand for the shop areas in backend/staff_access.py, so this page cannot hand out owner powers.
"""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import access, auditlog, staff_access

User = get_user_model()


def _managed(request, pk):
    """The user to change. Never yourself, never an owner: owners are not managed from this page."""
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk or access.is_owner(target) or target.is_superuser:
        raise PermissionDenied
    return target


def _grid_from_post(post):
    grid = staff_access.empty_grid()
    for area in staff_access.AREAS:
        for act in staff_access.ACTIONS:
            if post.get(f"p_{area['key']}_{act}") == '1':
                grid[area['key']].add(act)
    return staff_access.normalise(grid)


def _context(grid, *, target=None, values=None, errors=None, extra=0):
    can, cannot = staff_access.summary(grid)
    values = values or {}
    return dict(
        target=target, values=values, errors=errors or {}, extra=extra, grid=grid, role=staff_access.role_of(grid),
        areas=[dict(a, cells=[(act, staff_access.ACTION_LABELS[act], act in grid[a['key']]) for act in staff_access.ACTIONS])
               for a in staff_access.AREAS],
        actions=[(a, staff_access.ACTION_LABELS[a]) for a in staff_access.ACTIONS],
        roles=[dict(key=k, title=t, hint=h, grid={a: [x for x in staff_access.ACTIONS if x in v] for a, v in staff_access.role_grid(k).items()})
               for k, t, h, _ in staff_access.ROLES],
        can=can, cannot=cannot, risky=staff_access.risky(grid), owner_only=staff_access.OWNER_ONLY, active='users',
    )


def _clean_person(post, errors, *, new, target=None):
    values = {'full_name': (post.get('full_name') or '').strip(), 'username': (post.get('username') or '').strip(),
              'email': (post.get('email') or '').strip(), 'password': post.get('password') or ''}
    if len(values['full_name']) > 150:
        errors['full_name'] = 'Please use 150 characters or fewer.'
    if new:
        name = values['username']
        if not name:
            errors['username'] = 'Please type a sign-in name.'
        else:
            try:
                if len(name) > 150:
                    raise ValidationError('Please use 150 characters or fewer.')
                User.username_validator(name)
            except ValidationError as e:
                errors['username'] = e.messages[0]
            else:
                if User.objects.filter(username__iexact=name).exists():
                    errors['username'] = 'This sign-in name is already used. Please choose another one.'
    if values['email']:
        try:
            validate_email(values['email'])
        except ValidationError:
            errors['email'] = 'Please type a valid e-mail address.'
    if new or values['password']:
        probe = target or User(username=values['username'], email=values['email'])
        if not values['password']:
            errors['password'] = 'Please set a password.'
        else:
            try:
                validate_password(values['password'], probe)
            except ValidationError as e:
                errors['password'] = ' '.join(e.messages)
    return values


def _set_names(user, full_name):
    parts = full_name.split(None, 1)
    user.first_name = parts[0][:150] if parts else ''
    user.last_name = parts[1][:150] if len(parts) > 1 else ''


@access.owner_required
def staff_new(request):
    if request.method == 'POST':
        errors = {}
        values = _clean_person(request.POST, errors, new=True)
        grid = _grid_from_post(request.POST)
        if not errors:
            try:
                with transaction.atomic():
                    user = User(username=values['username'], email=values['email'], is_staff=False, is_superuser=False, is_active=True)
                    _set_names(user, values['full_name'])
                    user.set_password(values['password'])
                    user.save()
                    staff_access.apply(user, grid)
            except IntegrityError:
                errors['username'] = 'This sign-in name is already used. Please choose another one.'
            else:
                auditlog.record(request, 'USER ADDED', 'User', f"{user.username} ({staff_access.role_of(grid)})")
                messages.success(request, f"{user.username} was added as {staff_access.role_of(grid)}.")
                return redirect('users')
        values['password'] = ''
        return render(request, 'app/staff_form.html', _context(grid, values=values, errors=errors))
    return render(request, 'app/staff_form.html', _context(staff_access.role_grid('cashier'), values={}))


@access.owner_required
def staff_edit(request, pk):
    target = _managed(request, pk)
    if request.method == 'POST':
        errors = {}
        values = _clean_person(request.POST, errors, new=False, target=target)
        values['username'] = target.username
        grid = _grid_from_post(request.POST)
        if not errors:
            with transaction.atomic():
                target.email = values['email']
                _set_names(target, values['full_name'])
                if values['password']:
                    target.set_password(values['password'])
                target.save()
                staff_access.apply(target, grid)
            auditlog.record(request, 'USER CHANGED', 'User', f"{target.username} ({staff_access.role_of(grid)})"
                            + (', new password' if values['password'] else ''))
            messages.success(request, f"{target.username} was saved ({staff_access.role_of(grid)}).")
            return redirect('users')
        values['password'] = ''
        return render(request, 'app/staff_form.html', _context(grid, target=target, values=values, errors=errors,
                                                               extra=staff_access.extra_permissions(target)))
    values = {'full_name': target.get_full_name(), 'username': target.username, 'email': target.email}
    return render(request, 'app/staff_form.html', _context(staff_access.grid_of(target), target=target, values=values,
                                                           extra=staff_access.extra_permissions(target)))


@access.owner_required
@require_POST
def staff_active(request, pk):
    target = _managed(request, pk)
    target.is_active = not target.is_active
    target.save(update_fields=['is_active'])
    auditlog.record(request, 'USER ENABLED' if target.is_active else 'USER DISABLED', 'User', target.username)
    messages.success(request, f"{target.username} can sign in again." if target.is_active
                     else f"{target.username} is disabled and can no longer sign in.")
    return redirect('users')
