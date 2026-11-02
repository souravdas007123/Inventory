"""FastSelect must give exactly the HTML of Django's own Select (only faster)."""
from html.parser import HTMLParser

from django import forms
from django.test import TestCase

from backend.fastwidgets import FastSelect, speed_up
from backend.models import Product, Unit
from backend.tests.helpers import StockMixin


class Tree(HTMLParser):
    """The HTML as comparable data: tags with sorted attributes and the text, whitespace ignored."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        self.out.append(('<' + tag, tuple(sorted(attrs))))

    def handle_endtag(self, tag):
        self.out.append(('</' + tag, ()))

    def handle_data(self, data):
        if data.strip():
            self.out.append(('text', data.strip()))


def tree(html):
    t = Tree(); t.feed(str(html)); return t.out


CHOICES = [('', '---------'), ('1', 'Dolo 650'), ('2', 'A & B <i>"quoted"</i>'), ('3', "It's"), ('3', 'same value again')]


class SameAsDjangoTests(TestCase):
    def check(self, value, attrs=None, own=None):
        widget = {'attrs': own} if own else {}
        slow = forms.Select(choices=CHOICES, **widget).render('items-0-product', value, attrs)
        fast = FastSelect(choices=CHOICES, **widget).render('items-0-product', value, attrs)
        self.assertEqual(tree(fast), tree(slow), (value, attrs))
        return str(fast)

    def test_nothing_selected(self):
        self.check(None, {'id': 'id_x'})
        self.check('', {'id': 'id_x'})

    def test_a_selected_value_string_or_number(self):
        self.assertIn('<option value="1" selected>', self.check('1', {'id': 'id_x'}))
        self.check(1, {'id': 'id_x'})
        self.check('3', {'id': 'id_x'})               # two options with the same value: only the first is selected
        self.check('99', {'id': 'id_x'})              # a value that is not in the list

    def test_attributes_and_special_characters(self):
        self.check('2', {'id': 'id_x', 'required': True, 'class': 'a b', 'data-x': '"<&>'})
        self.check('2', {'id': 'id_x', 'disabled': False})
        self.check('1', None, own={'style': 'width:50px'})

    def test_text_is_escaped(self):
        html = self.check('2', {'id': 'id_x'})
        self.assertNotIn('<i>', html)
        self.assertIn('&lt;i&gt;', html)

    def test_option_groups_fall_back_to_django(self):
        grouped = [('', '---'), ('Group', [('1', 'One'), ('2', 'Two')])]
        self.assertEqual(tree(FastSelect(choices=grouped).render('n', '2')), tree(forms.Select(choices=grouped).render('n', '2')))

    def test_multiple_falls_back_to_django(self):
        class Multi(FastSelect):
            allow_multiple_selected = True
        self.assertEqual(tree(Multi(choices=CHOICES).render('n', ['1', '2'])), tree(forms.SelectMultiple(choices=CHOICES).render('n', ['1', '2'])))


class ModelFieldTests(StockMixin, TestCase):
    def test_a_model_choice_field_gives_the_same_html_before_and_after(self):
        for model, cls in ((Product, forms.ModelChoiceField), (Unit, forms.ModelChoiceField)):
            plain = cls(queryset=model.objects.all())
            fast = speed_up(cls(queryset=model.objects.all()))
            self.assertIsInstance(fast.widget, FastSelect)
            obj = model.objects.first()
            for value in (None, obj.pk):
                self.assertEqual(tree(fast.widget.render('f', value, {'id': 'id_f'})), tree(plain.widget.render('f', value, {'id': 'id_f'})))

    def test_a_customised_widget_is_left_alone(self):
        f = forms.ModelChoiceField(queryset=Unit.objects.all(), widget=forms.RadioSelect)
        self.assertIsInstance(speed_up(f).widget, forms.RadioSelect)

    def test_required_attribute_and_choices_survive(self):
        f = speed_up(forms.ModelChoiceField(queryset=Unit.objects.all(), required=True))
        html = f.widget.render('u', None, {'id': 'id_u', 'required': True})
        self.assertIn('required', html)
        self.assertEqual(html.count('<option'), Unit.objects.count() + 1)
