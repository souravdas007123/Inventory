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
