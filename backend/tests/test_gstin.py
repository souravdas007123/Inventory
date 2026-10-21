"""GSTIN check: shape, state code and check character. An old number already saved is not blocked until it is edited."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from backend import gstin, imports
from backend.models import GST_STATE_CODES, Customer, ShopProfile, Supplier

REAL = ['27AAPFU0939F1ZV', '07AAGFF2194N1Z1', '24AAACC1206D1ZM', '27AAACR5055K1Z7']      # well-known, valid numbers


def make(prefix14):
    return prefix14 + gstin.check_char(prefix14)


class GstinFunctionTests(TestCase):
    def test_real_numbers_pass_the_check_character(self):
        for g in REAL:
            with self.subTest(g=g):
                self.assertEqual(gstin.check_char(g[:14]), g[14])
                self.assertEqual(gstin.problem(g, GST_STATE_CODES), '')

    def test_blank_is_fine(self):
        for v in ('', None, '   '):
            self.assertEqual(gstin.problem(v, GST_STATE_CODES), '')

    def test_spaces_dashes_and_lower_case_are_cleaned(self):
        self.assertEqual(gstin.normalise(' 27aapfu0939f1zv '), '27AAPFU0939F1ZV')
        self.assertEqual(gstin.problem('27-AAPFU-0939-F1ZV', GST_STATE_CODES), '')

    def test_wrong_numbers_are_caught(self):
        bad = {
            '123456789012345': 'not valid',                  # random digits
            'ABCDEFGHIJKLMNO': 'not valid',
            '27AAPFU0939F1Z': '15 characters',               # too short
            '27AAPFU0939F1ZVV': '15 characters',             # too long
            '27AAPFU0939F1ZX': 'last character',             # one character wrong
            '27AAPFU0939F2ZV': 'last character',             # a digit changed
            '00AAPFU0939F1Z0': 'state code',                 # no such state
            '27AAPFU0939F1AV': 'not valid',                  # the 14th character must be Z
        }
        for g, word in bad.items():
            with self.subTest(g=g):
                self.assertIn(word, gstin.problem(g, GST_STATE_CODES))

    def test_every_state_code_is_accepted(self):
        for code in ('01', '25', '26', '28', '30', '31', '34', '35', '38', '97', '99'):
            self.assertIn(code, GST_STATE_CODES)
            g = make(code + 'AAAAA0000A1Z')
            self.assertEqual(gstin.problem(g, GST_STATE_CODES), '', g)


class GstinModelTests(TestCase):
    def test_supplier_rejects_a_random_number_and_accepts_a_real_one(self):
        s = Supplier(company='A', gstin='123456789012345')
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('gstin', ctx.exception.message_dict)
        s = Supplier(company='A', gstin=' 27aapfu0939f1zv ')
        s.full_clean()
        s.save()
        s.refresh_from_db()
        self.assertEqual((s.gstin, s.state, s.pan), ('27AAPFU0939F1ZV', 'Maharashtra', 'AAPFU0939F'))

    def test_a_goa_supplier_gets_its_state(self):
        g = make('30AAAAA0000A1Z')
        s = Supplier(company='Goa Pharma', gstin=g)
        s.full_clean()
        s.save()
        self.assertEqual(s.state, 'Goa')

    def test_blank_gstin_is_allowed_for_supplier_and_customer(self):
        s = Supplier(company='B', gstin='')
        s.full_clean()
        c = Customer(name='C', gstin='')
        c.full_clean()
        c.save()
        self.assertIsNone(Customer.objects.get(pk=c.pk).gstin)

    def test_customer_rejects_a_wrong_check_character(self):
        c = Customer(name='C', gstin='27AAPFU0939F1ZX')
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_shop_settings_check_the_number_too(self):
        shop = ShopProfile.get()
        shop.gstin = '123456789012345'
        with self.assertRaises(ValidationError):
            shop.full_clean()
        shop.gstin = '27AAPFU0939F1ZV'
        shop.full_clean()
        shop.gstin = ''
        shop.full_clean()

    def test_an_old_saved_number_does_not_block_editing_other_fields(self):
        s = Supplier.objects.create(company='Old', gstin='ABCDEFGHIJKLMNO')      # saved before the check existed
        s.refresh_from_db()
        s.city = 'Kolkata'
        s.full_clean()                                                            # the same old number is left alone
        s.gstin = '27AAPFU0939F1ZX'                                               # but a changed number is checked
        with self.assertRaises(ValidationError):
            s.full_clean()


class GstinImportTests(TestCase):
    def rows(self, kind, **raw):
        return imports.IMPORTERS[kind]['validate']([(2, raw)], {})

    def test_supplier_import_flags_a_bad_gstin_only_on_that_row(self):
        res = imports.IMPORTERS['suppliers']['validate'](
            [(2, {'company': 'Good', 'gstin': '27AAPFU0939F1ZV'}), (3, {'company': 'Bad', 'gstin': '123456789012345'})], {})
        self.assertEqual([r['action'] for r in res], ['create', 'error'])
        self.assertIn('123456789012345', ' '.join(res[1]['errors']))

    def test_customer_import_flags_a_bad_gstin(self):
        res = self.rows('customers', name='X', gstin='27AAPFU0939F1ZX')
        self.assertEqual(res[0]['action'], 'error')

    def test_an_existing_old_number_is_not_flagged_again(self):
        Supplier.objects.create(company='Old Co', gstin='ABCDEFGHIJKLMNO')
        res = imports.IMPORTERS['suppliers']['validate']([(2, {'company': 'Old Co', 'gstin': 'abcdefghijklmno'})], {'update': True})
        self.assertEqual(res[0]['action'], 'update')

    def test_the_example_number_in_the_templates_is_valid(self):
        for kind in ('customers', 'suppliers'):
            for c in imports.IMPORTERS[kind]['cols']:
                if c['name'] == 'gstin':
                    for sample in c['samples']:
                        self.assertEqual(gstin.problem(sample, GST_STATE_CODES), '', sample)
