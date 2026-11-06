"""A faster <select> for long lists (thousands of medicines).

Django renders every <option> through its template engine, which is the slowest part of the bill / purchase form once a shop has
a few thousand products. FastSelect builds the very same HTML directly. Anything unusual (option groups, multiple) falls back
to Django's own rendering, so the output never differs.
"""
from django import forms
from django.forms.utils import flatatt
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe


class FastSelect(forms.Select):
    def render(self, name, value, attrs=None, renderer=None):
        if self.allow_multiple_selected:
            return super().render(name, value, attrs, renderer)
        chosen = [str(v) for v in self.format_value(value)]
        parts, has_selected = [], False
        for option_value, label in self.choices:
            if isinstance(label, (list, tuple)):                       # an option group: let Django draw it
                return super().render(name, value, attrs, renderer)
            sval = '' if option_value is None else str(option_value)
            selected = sval in chosen and not has_selected
            has_selected = has_selected or selected
            parts.append(format_html('<option value="{}"{}>{}</option>', sval, mark_safe(' selected') if selected else '',
                                     conditional_escape(label)))
        return format_html('<select name="{}"{}>\n{}\n</select>', name, flatatt(self.build_attrs(self.attrs, attrs)),
                           mark_safe('\n'.join(parts)))


def speed_up(formfield):
    """Swap the plain Select of a model choice field for FastSelect (same attributes, same choices)."""
    if isinstance(formfield, forms.ModelChoiceField) and type(formfield.widget) is forms.Select:
        attrs = dict(formfield.widget.attrs)
        formfield.widget = FastSelect(attrs=attrs)
        formfield.widget.choices = formfield.choices
    return formfield


# ---------------------------------------------------------------------------------------------------------------------------
# Very long lists (thousands of medicines or customers): the page carries only the chosen option and the browser fetches the
# full list once (/api/choices/<kind>/) and fills a drop-down only when somebody uses it. The page stays small however many
# rows the bill or purchase has. Short lists are left alone, so a small shop sees exactly the same page as before.
# ---------------------------------------------------------------------------------------------------------------------------
LAZY_MIN = 150                       # a list with more entries than this is loaded on demand


def lazy_sources():
    """kind -> the queryset the form field uses. The field is made lazy only when it uses exactly this queryset."""
    from .models import Product
    from .models_extra import Customer
    return {'product': lambda: Product.objects.all(),
            'customer': lambda: Customer.objects.filter(is_active=True).order_by('name')}


class LazySelect(forms.Select):
    """Draws only the empty choice and the chosen option(s); the page script adds the rest when the drop-down is used."""

    def render(self, name, value, attrs=None, renderer=None):
        if self.allow_multiple_selected:
            return super().render(name, value, attrs, renderer)
        chosen = [str(v) for v in self.format_value(value) if str(v).isdigit()]
        parts = []
        empty = getattr(getattr(self.choices, 'field', None), 'empty_label', None)
        if empty is not None:
            parts.append(format_html('<option value="">{}</option>', empty))
        queryset = getattr(self.choices, 'queryset', None)
        if chosen and queryset is not None:
            field = self.choices.field
            for obj in queryset.filter(pk__in=chosen):
                parts.append(format_html('<option value="{}" selected>{}</option>', obj.pk, conditional_escape(field.label_from_instance(obj))))
        return format_html('<select name="{}"{}>\n{}\n</select>', name, flatatt(self.build_attrs(self.attrs, attrs)),
                           mark_safe('\n'.join(parts)))


def make_lazy(field, kind):
    widget = LazySelect(attrs={**field.widget.attrs, 'data-lazy': f'/api/choices/{kind}/'})
    widget.choices = field.choices
    widget.is_required = field.required
    field.widget = widget


def lazy_long_lists(form, formset=None):
    """Turn the product and customer drop-downs of this form (and of every row of its formset) into on-demand lists, when the list
    is long. One COUNT per kind, however many rows there are. True when something was made lazy."""
    sources, counts, made = lazy_sources(), {}, False
    all_forms = [form] + (list(formset.forms) + [formset.empty_form] if formset is not None else [])
    for f in all_forms:
        for kind, source in sources.items():
            field = f.fields.get(kind)
            if not isinstance(field, forms.ModelChoiceField) or isinstance(field.widget, LazySelect):
                continue
            expected = source()
            if field.queryset.model is not expected.model or str(field.queryset.query) != str(expected.query):
                continue                                      # a filtered list of its own: leave it as it is
            if kind not in counts:
                counts[kind] = expected.count()
            if counts[kind] > LAZY_MIN:
                make_lazy(field, kind)
                made = True
    return made


def keep_empty_form(FormSet):
    """A formset builds a brand new empty_form every time it is asked. Keep the first one, so a change made to its fields
    (the on-demand lists above) is what the page draws."""
    class Kept(FormSet):
        @property
        def empty_form(self):
            if getattr(self, '_kept_empty_form', None) is None:
                self._kept_empty_form = super().empty_form
            return self._kept_empty_form
    Kept.__name__ = FormSet.__name__
    return Kept
