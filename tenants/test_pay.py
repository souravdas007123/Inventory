"""Paying for a plan: Razorpay autopay subscriptions (stubbed), UPI with QR + approval, the webhook and the platform's Payments page.

Needs the tenants app (MULTI_SHOP=1). The shop is a plain row without a schema, and the shop-side views are reached on the main
address with the shop pinned, so the tests run the same on PostgreSQL and in a simple setup."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock, skipUnless
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, modify_settings, override_settings
from django.utils import timezone

from backend import qr, tenancy
from tenants import gst, razorpay, upi

HAS_TENANTS = 'tenants' in settings.INSTALLED_APPS
if HAS_TENANTS:
    from tenants import billing, invoicing, middleware, payshop, site
    from tenants.models import AutoPay, Invoice, InvoiceCounter, PlanOrder, Shop, SiteSetting

KEY, SECRET, HOOK = 'rzp_test_key', 'key-secret-123', 'hook-secret-456'
SELLER = dict(seller_name='Socialfly Test', seller_address='1 Test Road\nKolkata 700001', seller_gstin='19AABCU9603R1ZK',     # a valid number of West Bengal (code 19)
              seller_lut='AD190000000000A', seller_sac='998314')
RAZ = dict(RAZORPAY_KEY_ID=KEY, RAZORPAY_KEY_SECRET=SECRET, RAZORPAY_WEBHOOK_SECRET=HOOK, MULTI_SHOP=True,
           RAZORPAY_PLAN_MONTHLY='plan_M', RAZORPAY_PLAN_YEARLY='plan_Y')
HOST = dict(ALLOWED_HOSTS=['.localhost', 'testserver'], SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


def sign(body, secret):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeHttp:
    """Stands in for urllib.request.urlopen: answers by the end of the URL and remembers each request."""
    def __init__(self, answers):
        self.answers, self.seen = answers, []

    def __call__(self, req, timeout):
        self.seen.append(dict(url=req.full_url, body=json.loads(req.data) if req.data else None, auth=req.get_header('Authorization')))
        for end, out in self.answers.items():
            if req.full_url.endswith(end):
                if isinstance(out, Exception):
                    raise out
                return FakeResp(out)
        raise AssertionError('unexpected call ' + req.full_url)


class FakeResp:
    def __init__(self, out):
        self.out = out

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.out).encode()


GOOD_PLAN = {'plan_M': dict(period='monthly', interval=1, item=dict(amount=50000, currency='INR')),
             'plan_Y': dict(period='yearly', interval=1, item=dict(amount=500000, currency='INR'))}


class RazorpayPureTests(SimpleTestCase):
    def test_subscription_signature(self):
        good = sign(b'pay_1|sub_1', SECRET)
        self.assertTrue(razorpay.subscription_signature_ok('pay_1', 'sub_1', good, SECRET))
        for args in (('pay_2', 'sub_1', good), ('pay_1', 'sub_2', good), ('pay_1', 'sub_1', good[:-1] + ('1' if good[-1] == '0' else '0')), ('pay_1', 'sub_1', ''),
                     ('', 'sub_1', good), ('pay_1', '', good)):
            self.assertFalse(razorpay.subscription_signature_ok(*args, secret=SECRET), args)
        self.assertFalse(razorpay.subscription_signature_ok('pay_1', 'sub_1', good, ''))          # no secret, never valid
        self.assertFalse(razorpay.subscription_signature_ok('sub_1', 'pay_1', good, SECRET))      # the order of the two matters

    def test_webhook_signature(self):
        body = b'{"event":"subscription.charged"}'
        self.assertTrue(razorpay.webhook_signature_ok(body, sign(body, HOOK), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body + b' ', sign(body, HOOK), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, sign(body, 'other'), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, '', HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, sign(body, HOOK), ''))

    def test_paise(self):
        self.assertEqual((razorpay.paise(Decimal('500')), razorpay.paise('5000.00'), razorpay.paise(Decimal('499.5'))), (50000, 500000, 49950))

    @override_settings(**RAZ)
    def test_create_subscription_sends_the_right_request(self):
        fake = FakeHttp({'/subscriptions': {'id': 'sub_ABC'}})
        with mock.patch('urllib.request.urlopen', fake):
            self.assertEqual(razorpay.create_subscription('yearly', dict(shop='s_x')), 'sub_ABC')
        seen = fake.seen[0]
        self.assertEqual(seen['url'], 'https://api.razorpay.com/v1/subscriptions')
        self.assertEqual((seen['body']['plan_id'], seen['body']['total_count'], seen['body']['notes']), ('plan_Y', 10, dict(shop='s_x')))
        import base64
        self.assertEqual(base64.b64decode(seen['auth'].split()[1]).decode(), f'{KEY}:{SECRET}')

    @override_settings(**RAZ)
    def test_cancel_stops_at_the_end_of_the_paid_period_by_default(self):
        fake = FakeHttp({'/subscriptions/sub_1/cancel': {'id': 'sub_1'}})
        with mock.patch('urllib.request.urlopen', fake):
            razorpay.cancel_subscription('sub_1')
            razorpay.cancel_subscription('sub_1', at_cycle_end=False)
        self.assertEqual([c['body'] for c in fake.seen], [dict(cancel_at_cycle_end=1), dict(cancel_at_cycle_end=0)])

    @override_settings(**RAZ)
    def test_the_razorpay_plan_must_be_the_one_we_show(self):
        with mock.patch('urllib.request.urlopen', FakeHttp({'/plans/plan_M': GOOD_PLAN['plan_M'], '/plans/plan_Y': GOOD_PLAN['plan_Y']})):
            razorpay.check_plan('monthly', 500)
            razorpay.check_plan('yearly', Decimal('5000.00'))
        for plan_id, price in (('plan_M', 600), ('plan_Y', 4000)):               # the price was changed here but not at Razorpay
            with mock.patch('urllib.request.urlopen', FakeHttp({f'/plans/{plan_id}': GOOD_PLAN[plan_id]})):
                with self.assertRaises(razorpay.RazorpayError):
                    razorpay.check_plan('monthly' if plan_id == 'plan_M' else 'yearly', price)
        wrong = dict(GOOD_PLAN['plan_M'], period='weekly')                         # right amount, wrong period
        with mock.patch('urllib.request.urlopen', FakeHttp({'/plans/plan_M': wrong})):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.check_plan('monthly', 500)
        every2 = dict(GOOD_PLAN['plan_M'], interval=2)
        with mock.patch('urllib.request.urlopen', FakeHttp({'/plans/plan_M': every2})):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.check_plan('monthly', 500)

    @override_settings(**RAZ)
    def test_failures_are_polite(self):
        import io
        import urllib.error
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('down')):
            with self.assertRaises(razorpay.RazorpayError) as cm:
                razorpay.create_subscription('monthly')
        self.assertIn('could not reach', str(cm.exception))
        err = urllib.error.HTTPError('u', 400, 'Bad', {}, io.BytesIO(b'{"error":{"description":"secret detail"}}'))
        with mock.patch('urllib.request.urlopen', side_effect=err):
            with self.assertRaises(razorpay.RazorpayError) as cm:
                razorpay.create_subscription('monthly')
        self.assertNotIn('secret detail', str(cm.exception))                       # Razorpay's own text is only logged
        with mock.patch('urllib.request.urlopen', FakeHttp({'/subscriptions': {}})):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.create_subscription('monthly')
        with mock.patch('urllib.request.urlopen', FakeHttp({'/subscriptions': [1]})):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.create_subscription('monthly')

    def test_configured_needs_both_keys_and_many_shops(self):
        for keys, expect in ((dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True), True),
                             (dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='', MULTI_SHOP=True), False),
                             (dict(RAZORPAY_KEY_ID='', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True), False),
                             (dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=False), False)):
            with override_settings(**keys):
                self.assertEqual(razorpay.configured(), expect, keys)

    def test_a_plan_can_be_bought_only_with_its_razorpay_plan_id(self):
        base = dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True)
        with override_settings(RAZORPAY_PLAN_MONTHLY='plan_M', RAZORPAY_PLAN_YEARLY='', **base):
            self.assertEqual((razorpay.available('monthly'), razorpay.available('yearly'), razorpay.available('weekly')), (True, False, False))
        with override_settings(RAZORPAY_PLAN_MONTHLY='plan_M', RAZORPAY_PLAN_YEARLY='plan_Y', RAZORPAY_KEY_ID='', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True):
            self.assertFalse(razorpay.available('monthly'))


class UpiPureTests(SimpleTestCase):
    def test_the_reference_number_must_be_12_digits(self):
        self.assertEqual(upi.clean_utr(' 4012 3456 7890 '), ('401234567890', ''))
        for bad in ('', '12345', '40123456789a', '4012345678901', '<script>', None):
            self.assertTrue(upi.clean_utr(bad)[1], bad)

    def test_the_link_has_everything_and_fits_a_qr_code(self):
        link = upi.pay_link('medsync@okhdfcbank', 'MedSync & Co', '500', 'MS7K2Q9XAB', 'Monthly')
        self.assertTrue(link.startswith('upi://pay?'))
        q = parse_qs(urlparse(link).query)
        self.assertEqual((q['pa'], q['am'], q['cu'], q['pn']), (['medsync@okhdfcbank'], ['500.00'], ['INR'], ['MedSync & Co']))
        self.assertIn('MS7K2Q9XAB', q['tn'][0])
        self.assertTrue(qr.svg(link))
        long = upi.pay_link('a' * 60 + '@okhdfcbank', 'N' * 40, '5000', 'MS7K2Q9XAB', 'Yearly')
        self.assertEqual(len(parse_qs(urlparse(long).query)['pn'][0]), 30)

    def test_an_odd_name_cannot_break_the_link(self):
        link = upi.pay_link('a@b', 'x&am=1&pa=evil@bank', '500', 'MSAAAAAAAA', 'Monthly')
        q = parse_qs(urlparse(link).query)
        self.assertEqual((q['pa'], q['am']), (['a@b'], ['500.00']))


class PinTenantMiddleware:
    """Makes the request belong to the shop under test (the tests reach the shop's pages on the main address)."""
    shop = None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if PinTenantMiddleware.shop is not None:
            request.tenant = PinTenantMiddleware.shop
        return self.get_response(request)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**HOST)
@modify_settings(MIDDLEWARE={'append': 'tenants.test_pay.PinTenantMiddleware'})
class PayTestBase(TestCase):
    def setUp(self):
        cache.clear()
        today = timezone.localdate()
        self.shop = Shop(schema_name='s_pay_test', name='Sharma Medical', owner_name='Raj Sharma', email='raj@example.com', phone='9876543210',
                         plan='trial', trial_ends=today + timedelta(days=3), email_verified=True,
                         bill_name='Sharma Medical', bill_country='IN', bill_state='19', bill_address='5 Park Street\nKolkata')
        self.shop.auto_create_schema = False                      # a plain row: no schema is needed for these tests
        self.shop.save()
        site.save(dict(SELLER))
        self.admin = get_user_model().objects.create_superuser('boss', 'b@example.com', 'pw-12345')
        self.client.force_login(self.admin)
        self.today = today
        mail.outbox.clear()

    def on_shop(self):
        """Opens the shop-side views as the owner of self.shop."""
        patches = [mock.patch('tenants.payshop._shop', return_value=self.shop), mock.patch.object(tenancy, 'on_shop_site', return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        PinTenantMiddleware.shop = self.shop
        self.addCleanup(setattr, PinTenantMiddleware, 'shop', None)

    def set_upi(self, upi_id='medsync@okhdfcbank', name='MedSync'):
        site.save(dict(upi_id=upi_id, upi_name=name))
        cache.clear()

    def order(self, **kw):
        base = dict(shop=self.shop, plan='monthly', amount=Decimal('500'), method='upi', reference=billing.new_reference())
        base.update(kw)
        return PlanOrder.objects.create(**base)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class SettleTests(PayTestBase):
    def test_a_paid_order_extends_the_shop_once_and_mails_a_receipt(self):
        o = self.order(status='waiting', utr='401234567890')
        done = billing.settle(o.pk, 'UPI 401234567890, approved')
        self.assertIsNotNone(done)
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.plan, self.shop.paid_until), ('monthly', tenancy.add_months(self.today, 1)))
        o.refresh_from_db()
        self.assertEqual((o.status, o.payment.amount, o.payment.period_from), ('paid', Decimal('500.00'), self.today))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Rs 500', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['raj@example.com'])
        self.assertIsNone(billing.settle(o.pk, 'again'))                       # a second confirmation does nothing
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))
        self.assertEqual(self.shop.payments.count(), 1)

    def test_a_payment_before_the_end_starts_after_the_paid_period(self):
        until = self.today + timedelta(days=10)
        Shop.objects.filter(pk=self.shop.pk).update(plan='monthly', paid_until=until)
        o = self.order(plan='yearly', amount=Decimal('5000'), status='waiting')
        billing.settle(o.pk, 'x', allowed=('waiting',))
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.plan, self.shop.paid_until), ('yearly', tenancy.add_months(until, 12)))

    def test_only_the_allowed_states_can_be_paid(self):
        for status in ('rejected', 'cancelled', 'paid'):
            o = self.order(status=status)
            self.assertIsNone(billing.settle(o.pk, 'x', allowed=('created', 'waiting')), status)
        self.assertEqual(self.shop.payments.count(), 0)
        o = self.order(status='cancelled')
        self.assertIsNotNone(billing.settle(o.pk, 'late webhook', allowed=('created', 'waiting', 'cancelled')))

    def test_reject_only_from_waiting_and_tells_the_shop(self):
        o = self.order(status='waiting', utr='401234567890')
        self.assertIsNotNone(billing.reject(o.pk, 'Not in our account'))
        o.refresh_from_db()
        self.assertEqual((o.status, o.note), ('rejected', 'Not in our account'))
        self.assertIn('Not in our account', mail.outbox[0].body)
        self.assertIsNone(billing.reject(o.pk, 'again'))
        self.assertEqual(self.shop.payments.count(), 0)

    def test_references_are_unique_and_easy_to_read(self):
        refs = {billing.new_reference() for _ in range(50)}
        self.assertEqual(len(refs), 50)
        for r in refs:
            self.assertRegex(r, r'^MS[A-HJ-NP-Z2-9]{8}$')

    def test_the_panels_save_payment_button_still_uses_the_same_rule(self):
        from django.test import RequestFactory
        from tenants import platform
        req = RequestFactory().post('/x/', dict(action='paid', plan='monthly', amount='', note='cash'))
        msg = platform._apply(req, self.shop, self.today)
        self.assertIn('Payment of Rs 500', msg)
        self.assertEqual((self.shop.plan, self.shop.paid_until), ('monthly', tenancy.add_months(self.today, 1)))
        self.assertEqual(self.shop.payments.get().note, 'cash')


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class PeriodTests(PayTestBase):
    def test_the_period_starts_after_the_paid_one(self):
        self.assertEqual(billing.period_for(self.shop, 'monthly', self.today), (self.today, tenancy.add_months(self.today, 1)))
        Shop.objects.filter(pk=self.shop.pk).update(paid_until=self.today + timedelta(days=10))
        self.shop.refresh_from_db()
        start = self.today + timedelta(days=10)
        self.assertEqual(billing.period_for(self.shop, 'yearly', self.today), (start, tenancy.add_months(start, 12)))


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**RAZ)
class AutoPayTests(PayTestBase):
    def start(self, plan='monthly', sub='sub_XYZ'):
        self.on_shop()
        with mock.patch('tenants.razorpay.check_plan') as cp, mock.patch('tenants.razorpay.create_subscription', return_value=sub) as cs, \
                mock.patch('tenants.razorpay.cancel_subscription') as cx:
            r = self.client.post('/plan/pay/autopay/', dict(plan=plan))
        return r, cp, cs, cx

    def verify(self, pay='pay_777', sub='sub_XYZ', sig=None):
        return self.client.post('/plan/pay/autopay/verify/', dict(
            razorpay_subscription_id=sub, razorpay_payment_id=pay, razorpay_signature=sign(f'{pay}|{sub}'.encode(), SECRET) if sig is None else sig))

    def test_start_makes_a_subscription_at_the_server_price(self):
        r, cp, cs, _ = self.start('yearly')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        a = AutoPay.objects.get()
        self.assertEqual((data['ok'], data['key'], data['subscription_id']), (True, KEY, 'sub_XYZ'))
        self.assertEqual((a.plan, a.amount, a.status, a.razorpay_subscription_id), ('yearly', Decimal('5000'), 'created', 'sub_XYZ'))
        self.assertEqual(cp.call_args.args, ('yearly', Decimal('5000')))          # the Razorpay plan is checked against our price
        self.assertEqual(cs.call_args.args[0], 'yearly')
        self.assertNotIn(SECRET, r.content.decode())                              # the secret never leaves the server
        self.assertIn('renews every year', data['description'])

    def test_the_browser_cannot_choose_the_amount(self):
        self.on_shop()
        with mock.patch('tenants.razorpay.check_plan'), mock.patch('tenants.razorpay.create_subscription', return_value='sub_1'):
            self.client.post('/plan/pay/autopay/', dict(plan='monthly', amount='1', plan_id='plan_other'))
        self.assertEqual(AutoPay.objects.get().amount, Decimal('500'))

    def test_a_wrong_plan_an_unready_plan_and_a_failing_service(self):
        self.on_shop()
        self.assertEqual(self.client.post('/plan/pay/autopay/', dict(plan='weekly')).status_code, 400)
        with override_settings(RAZORPAY_PLAN_YEARLY=''):
            self.assertEqual(self.client.post('/plan/pay/autopay/', dict(plan='yearly')).status_code, 404)
        with override_settings(RAZORPAY_KEY_ID=''):
            self.assertEqual(self.client.post('/plan/pay/autopay/', dict(plan='monthly')).status_code, 404)
        with mock.patch('tenants.razorpay.check_plan', side_effect=razorpay.RazorpayError('Autopay is not ready for this plan yet.')):
            r = self.client.post('/plan/pay/autopay/', dict(plan='monthly'))
        self.assertEqual((r.status_code, r.json()['ok']), (502, False))
        self.assertEqual(AutoPay.objects.count(), 0)
        self.assertEqual(self.client.get('/plan/pay/autopay/').status_code, 405)

    def test_one_active_autopay_at_a_time_and_a_limit_per_day(self):
        AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_ACTIVE', status='active')
        r, _, cs, _ = self.start('yearly')
        self.assertEqual(r.status_code, 409)
        cs.assert_not_called()
        AutoPay.objects.all().delete()
        for i in range(payshop.MAX_ORDERS_PER_DAY):
            AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id=f'sub_old{i}', status='cancelled')
        self.assertEqual(self.start()[0].status_code, 429)

    def test_an_unfinished_checkout_is_cancelled_when_a_new_one_starts(self):
        AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_STALE')
        r, _, _, cx = self.start('yearly')
        self.assertEqual(r.status_code, 200)
        cx.assert_called_once_with('sub_STALE', at_cycle_end=False)
        self.assertEqual(AutoPay.objects.get(razorpay_subscription_id='sub_STALE').status, 'cancelled')

    def test_a_real_signature_extends_the_plan_and_switches_autopay_on(self):
        self.start()
        r = self.verify()
        self.assertEqual((r.status_code, r.json()), (200, {'ok': True}))
        a = AutoPay.objects.get()
        o = PlanOrder.objects.get()
        self.assertEqual((a.status, o.status, o.razorpay_payment_id, o.autopay_id, o.amount, o.method), ('active', 'paid', 'pay_777', a.pk, Decimal('500'), 'razorpay'))
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.plan, self.shop.paid_until), ('monthly', tenancy.add_months(self.today, 1)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('MS/', mail.outbox[0].body)                                # the invoice number
        self.assertIn('CGST (9%): Rs 38.13', mail.outbox[0].body)
        self.assertIn('SGST (9%): Rs 38.13', mail.outbox[0].body)
        self.assertIn('renews on its own', mail.outbox[0].body)

    def test_a_forged_or_foreign_confirmation_changes_nothing(self):
        self.start()
        for sub, pay, sig in (('sub_XYZ', 'pay_1', 'deadbeef'), ('sub_XYZ', 'pay_1', sign(b'pay_2|sub_XYZ', SECRET)),
                              ('sub_XYZ', 'pay_1', sign(b'sub_XYZ|pay_1', SECRET)), ('sub_NOPE', 'pay_1', sign(b'pay_1|sub_NOPE', SECRET)),
                              ('', '', '')):
            self.assertEqual(self.verify(pay, sub, sig).status_code, 400, (sub, pay))
        self.assertEqual(PlanOrder.objects.count(), 0)
        self.assertEqual(AutoPay.objects.get().status, 'created')
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.paid_until)

    def test_another_shops_subscription_cannot_be_confirmed(self):
        other = Shop(schema_name='s_other_test', name='Other', email='o@example.com', plan='trial')
        other.auto_create_schema = False
        other.save()
        AutoPay.objects.create(shop=other, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_OTHER')
        self.on_shop()
        self.assertEqual(self.verify('pay_1', 'sub_OTHER').status_code, 400)
        self.assertEqual(AutoPay.objects.get().status, 'created')
        self.assertEqual(PlanOrder.objects.count(), 0)

    def test_confirming_twice_never_extends_twice(self):
        self.start()
        self.assertTrue(self.verify().json()['ok'])
        self.assertTrue(self.verify().json()['ok'])
        self.assertEqual(self.shop.payments.count(), 1)
        self.assertEqual(PlanOrder.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_cancel_stops_the_renewals_but_keeps_the_paid_days(self):
        self.start()
        self.verify()
        a = AutoPay.objects.get()
        with mock.patch('tenants.razorpay.cancel_subscription') as cx:
            r = self.client.post(f'/plan/pay/autopay/{a.pk}/cancel/')
        self.assertRedirects(r, '/plan/', fetch_redirect_response=False)
        cx.assert_called_once_with('sub_XYZ', at_cycle_end=True)
        a.refresh_from_db()
        self.assertEqual(a.status, 'cancelled')
        self.assertIsNotNone(a.ended_at)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))

    def test_a_failing_cancel_leaves_autopay_on_and_says_so(self):
        self.start()
        self.verify()
        a = AutoPay.objects.get()
        with mock.patch('tenants.razorpay.cancel_subscription', side_effect=razorpay.RazorpayError('We could not reach the payment service.')):
            r = self.client.post(f'/plan/pay/autopay/{a.pk}/cancel/', follow=True)
        a.refresh_from_db()
        self.assertEqual(a.status, 'active')
        self.assertContains(r, 'The autopay was not stopped')

    def test_cancel_is_for_the_owner_of_the_shop_only(self):
        other = Shop(schema_name='s_other_test', name='Other', email='o@example.com', plan='trial')
        other.auto_create_schema = False
        other.save()
        a = AutoPay.objects.create(shop=other, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_OTHER', status='active')
        self.on_shop()
        with mock.patch('tenants.razorpay.cancel_subscription') as cx:
            self.assertEqual(self.client.post(f'/plan/pay/autopay/{a.pk}/cancel/').status_code, 404)
            self.assertEqual(self.client.get(f'/plan/pay/autopay/{a.pk}/cancel/').status_code, 405)
        cx.assert_not_called()
        a.refresh_from_db()
        self.assertEqual(a.status, 'active')


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**RAZ)
class WebhookTests(PayTestBase):
    def event(self, name='subscription.charged', sub='sub_XYZ', amount=50000, pay_id='pay_9', charge_at=None):
        payload = dict(subscription=dict(entity=dict(id=sub, charge_at=charge_at)))
        if name == 'subscription.charged':
            payload['payment'] = dict(entity=dict(id=pay_id, amount=amount, status='captured'))
        return json.dumps(dict(event=name, payload=payload)).encode()

    def post(self, body, secret=HOOK, **extra):
        return self.client.post('/razorpay/webhook/', body, content_type='application/json',
                                HTTP_X_RAZORPAY_SIGNATURE=sign(body, secret) if secret else '', **extra)

    def setUp(self):
        super().setUp()
        self.client.logout()                                    # Razorpay is not signed in
        self.ap = AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_XYZ')

    def test_a_signed_charge_extends_the_plan_once(self):
        r = self.post(self.event())
        self.assertEqual((r.status_code, r.content), (200, b'ok'))
        self.ap.refresh_from_db()
        o = PlanOrder.objects.get()
        self.assertEqual((self.ap.status, o.status, o.razorpay_payment_id, o.autopay_id), ('active', 'paid', 'pay_9', self.ap.pk))
        self.post(self.event())                                  # Razorpay sends it again
        self.assertEqual((self.shop.payments.count(), PlanOrder.objects.count(), len(mail.outbox)), (1, 1, 1))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))

    def test_every_renewal_adds_another_period(self):
        self.post(self.event(pay_id='pay_1'))
        self.post(self.event(pay_id='pay_2'))                    # next month
        self.post(self.event(pay_id='pay_3'))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.payments.count(), 3)
        self.assertEqual(self.shop.paid_until, tenancy.add_months(tenancy.add_months(tenancy.add_months(self.today, 1), 1), 1))

    def test_the_next_charge_day_is_kept(self):
        stamp = int(timezone.now().timestamp()) + 30 * 86400
        self.post(self.event(charge_at=stamp))
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.next_charge, (timezone.now() + timedelta(days=30)).astimezone(timezone.get_current_timezone()).date())

    def test_the_browser_and_the_webhook_together_extend_once(self):
        self.post(self.event(pay_id='pay_5'))                    # the webhook came first (common)
        self.client.force_login(self.admin)
        self.on_shop()
        r = self.client.post('/plan/pay/autopay/verify/', dict(razorpay_subscription_id='sub_XYZ', razorpay_payment_id='pay_5',
                                                              razorpay_signature=sign(b'pay_5|sub_XYZ', SECRET)))
        self.assertEqual(r.json(), {'ok': True})
        self.assertEqual((self.shop.payments.count(), PlanOrder.objects.count()), (1, 1))

    def test_a_bad_or_missing_signature_is_refused(self):
        body = self.event()
        self.assertEqual(self.post(body, secret='wrong').status_code, 400)
        self.assertEqual(self.post(body, secret='').status_code, 400)
        self.assertEqual(self.client.post('/razorpay/webhook/', body, content_type='application/json').status_code, 400)
        self.assertEqual(PlanOrder.objects.count(), 0)
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'created')

    def test_a_wrong_amount_unknown_subscription_and_other_events_change_nothing(self):
        self.assertEqual(self.post(self.event(amount=100)).content, b'amount differs')
        self.assertEqual(self.post(self.event(sub='sub_NOPE')).content, b'unknown subscription')
        self.assertEqual(self.post(self.event(name='payment.failed')).content, b'ignored')
        self.assertEqual(self.post(self.event(name='subscription.authenticated')).content, b'ignored')
        self.assertEqual(self.post(b'[1,2]').status_code, 200)
        self.assertEqual(PlanOrder.objects.count(), 0)
        self.assertEqual(self.shop.payments.count(), 0)

    def test_garbage_that_is_signed_is_a_400(self):
        self.assertEqual(self.post(b'not json').status_code, 400)

    def test_without_a_webhook_secret_the_page_does_not_exist(self):
        with override_settings(RAZORPAY_WEBHOOK_SECRET=''):
            self.assertEqual(self.post(self.event()).status_code, 404)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get('/razorpay/webhook/').status_code, 405)

    def test_cancelled_halted_and_completed_events(self):
        self.post(self.event(pay_id='pay_1'))
        self.assertEqual(self.post(self.event('subscription.halted')).content, b'ok')
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'halted')
        self.assertIsNotNone(self.ap.ended_at)
        self.assertEqual(len(mail.outbox), 2)                    # the receipt and the "payment did not go through" mail
        self.assertIn('did not go through', mail.outbox[1].subject)
        self.post(self.event('subscription.halted'))             # sent again: no second mail
        self.assertEqual(len(mail.outbox), 2)
        self.post(self.event('subscription.cancelled'))
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'cancelled')
        self.assertEqual(self.ap.status, 'cancelled')

    def test_a_subscription_that_ran_its_course_is_completed(self):
        self.post(self.event(pay_id='pay_1'))
        self.post(self.event('subscription.completed'))
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'completed')

    def test_a_late_event_never_undoes_a_stop(self):
        self.post(self.event(pay_id='pay_1'))
        self.post(self.event('subscription.cancelled'))
        self.post(self.event('subscription.activated'))          # arrives late
        self.post(self.event('subscription.halted'))
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'cancelled')

    def test_the_last_charge_after_a_cancel_is_still_credited_and_the_status_stays(self):
        self.post(self.event(pay_id='pay_1'))
        self.post(self.event('subscription.cancelled'))
        self.post(self.event(pay_id='pay_2'))                    # a charge that was already on its way
        self.ap.refresh_from_db()
        self.assertEqual((self.ap.status, self.shop.payments.count()), ('cancelled', 2))

    def test_a_charge_after_a_failure_switches_autopay_on_again(self):
        self.post(self.event(pay_id='pay_1'))
        self.post(self.event('subscription.halted'))
        self.post(self.event(pay_id='pay_2'))
        self.ap.refresh_from_db()
        self.assertEqual(self.ap.status, 'active')


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class UpiFlowTests(PayTestBase):
    def test_no_upi_id_means_no_upi_payment(self):
        self.on_shop()
        self.assertEqual(self.client.post('/plan/pay/upi/', dict(plan='monthly')).status_code, 404)

    def test_the_qr_page_shows_the_amount_the_id_and_a_qr_code(self):
        self.set_upi()
        self.on_shop()
        r = self.client.post('/plan/pay/upi/', dict(plan='yearly'))
        o = PlanOrder.objects.get()
        self.assertRedirects(r, f'/plan/pay/upi/{o.pk}/', fetch_redirect_response=False)
        self.assertEqual((o.method, o.status, o.amount, o.plan), ('upi', 'created', Decimal('5000'), 'yearly'))
        page = self.client.get(f'/plan/pay/upi/{o.pk}/').content.decode()
        self.assertIn('medsync@okhdfcbank', page)
        self.assertIn('Rs 5000.00', page)
        self.assertIn(o.reference, page)
        self.assertIn('<svg', page)
        self.assertIn('upi://pay?pa=medsync@okhdfcbank', page)

    def test_the_same_plan_does_not_make_a_new_order_each_time(self):
        self.set_upi()
        self.on_shop()
        self.client.post('/plan/pay/upi/', dict(plan='monthly'))
        self.client.post('/plan/pay/upi/', dict(plan='monthly'))
        self.assertEqual(PlanOrder.objects.count(), 1)

    def test_a_bad_reference_number_is_refused_with_a_message(self):
        self.set_upi()
        self.on_shop()
        o = self.order()
        r = self.client.post(f'/plan/pay/upi/{o.pk}/', dict(utr='123'))
        self.assertContains(r, '12-digit')
        o.refresh_from_db()
        self.assertEqual(o.status, 'created')

    def test_a_good_reference_number_goes_to_the_platform_owner(self):
        self.set_upi()
        site.save(dict(upi_id='medsync@okhdfcbank', upi_name='MedSync', contact_email='owner@example.com'))
        cache.clear()
        self.on_shop()
        o = self.order()
        r = self.client.post(f'/plan/pay/upi/{o.pk}/', dict(utr='4012 3456 7890'))
        self.assertRedirects(r, '/plan/', fetch_redirect_response=False)
        o.refresh_from_db()
        self.assertEqual((o.status, o.utr), ('waiting', '401234567890'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['owner@example.com'])
        self.assertIn('401234567890', mail.outbox[0].body)
        self.assertIn('/superadmin/payments/', mail.outbox[0].body)
        self.assertEqual(self.shop.payments.count(), 0)                        # nothing is credited before approval

    def test_a_reference_number_cannot_be_used_twice(self):
        self.set_upi()
        self.on_shop()
        self.order(status='waiting', utr='401234567890')
        o = self.order()
        r = self.client.post(f'/plan/pay/upi/{o.pk}/', dict(utr='401234567890'))
        self.assertContains(r, 'already used')
        self.order(status='rejected', utr='111111111111')                       # a rejected number may be tried again
        self.assertRedirects(self.client.post(f'/plan/pay/upi/{o.pk}/', dict(utr='111111111111')), '/plan/', fetch_redirect_response=False)

    def test_a_sent_payment_page_goes_back_to_the_plan_page(self):
        self.set_upi()
        self.on_shop()
        o = self.order(status='waiting', utr='401234567890')
        self.assertRedirects(self.client.get(f'/plan/pay/upi/{o.pk}/'), '/plan/', fetch_redirect_response=False)

    def test_another_shops_order_is_not_found(self):
        self.set_upi()
        self.on_shop()
        other = Shop(schema_name='s_other_test', name='Other', email='o@example.com', plan='trial')
        other.auto_create_schema = False
        other.save()
        o = PlanOrder.objects.create(shop=other, plan='monthly', amount=Decimal('500'), method='upi', reference='MSOTHER222')
        self.assertEqual(self.client.get(f'/plan/pay/upi/{o.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/plan/pay/{o.pk}/cancel/').status_code, 302)
        o.refresh_from_db()
        self.assertEqual(o.status, 'created')

    def test_cancel(self):
        self.set_upi()
        self.on_shop()
        o = self.order()
        self.client.post(f'/plan/pay/{o.pk}/cancel/')
        o.refresh_from_db()
        self.assertEqual(o.status, 'cancelled')

    def test_a_qr_that_is_too_long_leaves_the_picture_out_but_keeps_the_id(self):
        self.set_upi(upi_id='a' * 64 + '@okhdfcbank', name='N' * 40)
        self.on_shop()
        o = self.order()
        page = self.client.get(f'/plan/pay/upi/{o.pk}/').content.decode()
        self.assertIn('a' * 64 + '@okhdfcbank', page)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**RAZ)
class PlanPageTests(PayTestBase):
    def page(self):
        self.on_shop()
        return self.client.get('/plan/')

    def test_buttons_follow_what_is_switched_on(self):
        html = self.page().content.decode()
        self.assertIn('data-pay="autopay" data-plan="monthly"', html)
        self.assertIn('data-pay="autopay" data-plan="yearly"', html)
        self.assertNotIn('/plan/pay/upi/', html)
        self.assertIn('https://checkout.razorpay.com/v1/checkout.js', html)         # loaded only when the button is pressed
        self.assertIn('GST', html)
        self.set_upi()
        html = self.page().content.decode()
        self.assertIn('action="/plan/pay/upi/"', html)
        with override_settings(RAZORPAY_KEY_ID=''):
            html = self.page().content.decode()
        self.assertNotIn('data-pay="autopay"', html)
        self.assertIn('Pay by UPI', html)
        with override_settings(RAZORPAY_PLAN_YEARLY=''):                           # only the plans that exist at Razorpay
            html = self.page().content.decode()
        self.assertIn('data-pay="autopay" data-plan="monthly"', html)
        self.assertNotIn('data-pay="autopay" data-plan="yearly"', html)

    def test_the_popup_before_paying_has_price_gst_total_and_days(self):
        html = self.page().content.decode()
        self.assertIn('id="paysum"', html)
        end = tenancy.add_months(self.today, 1)
        for text in ('"total": "500.00"', '"total": "5000.00"', f'"days": {(end - self.today).days}', f'"end": "{end:%d %b %Y}"',
                     '["Price without GST", "Rs 423.74"]', '["CGST (9%)", "Rs 38.13"]', '["SGST (9%)", "Rs 38.13"]', '["Total to pay now", "Rs 500.00"]',
                     '["Price without GST", "Rs 4237.30"]', 'Continue to payment'):
            self.assertIn(text, html)

    def test_the_popup_follows_the_state_and_country_of_the_customer(self):
        Shop.objects.filter(pk=self.shop.pk).update(bill_state='27')                     # another state: IGST
        self.shop.refresh_from_db()
        html = self.page().content.decode()
        self.assertIn('["IGST (18%)", "Rs 76.27"]', html)
        self.assertNotIn('CGST', html.split('id="paysum-data"')[1].split('</script>')[0])
        Shop.objects.filter(pk=self.shop.pk).update(bill_country='US', bill_state='')    # another country: export, no tax
        self.shop.refresh_from_db()
        html = self.page().content.decode()
        self.assertIn('["Tax (export of service, zero-rated)", "Rs 0.00"]', html)
        self.assertIn('["Total to pay now", "Rs 500.00"]', html)

    def test_without_invoice_details_the_pay_buttons_wait(self):
        self.set_upi()
        Shop.objects.filter(pk=self.shop.pk).update(bill_address='')
        self.shop.refresh_from_db()
        html = self.page().content.decode()
        self.assertIn('Please save your invoice details above first', html)
        self.assertNotIn('data-pay="autopay"', html)
        self.assertNotIn('action="/plan/pay/upi/"', html)
        self.assertIn('needed before you pay', html)

    def test_the_popup_says_when_the_new_days_start(self):
        until = self.today + timedelta(days=10)
        Shop.objects.filter(pk=self.shop.pk).update(plan='monthly', paid_until=until)
        self.shop.refresh_from_db()
        self.on_shop()
        html = self.client.get('/plan/').content.decode()
        self.assertIn('"after_current": true', html)
        self.assertIn(f'"start": "{until:%d %b %Y}"', html)

    def test_an_active_autopay_is_shown_with_a_stop_button_and_no_second_subscribe(self):
        a = AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_1', status='active',
                                   next_charge=self.today + timedelta(days=20))
        Shop.objects.filter(pk=self.shop.pk).update(plan='monthly', paid_until=self.today + timedelta(days=20))
        self.shop.refresh_from_db()
        self.set_upi()
        html = self.page().content.decode()
        self.assertIn('Autopay is on', html)
        self.assertIn(f'/plan/pay/autopay/{a.pk}/cancel/', html)
        self.assertNotIn('data-pay="autopay"', html)
        self.assertIn('Pay by UPI', html)

    def test_a_failed_autopay_is_explained(self):
        AutoPay.objects.create(shop=self.shop, plan='monthly', amount=Decimal('500'), razorpay_subscription_id='sub_1', status='halted',
                               ended_at=timezone.now())
        html = self.page().content.decode()
        self.assertIn('could not be taken', html)
        self.assertIn('data-pay="autopay" data-plan="monthly"', html)

    def test_nothing_to_pay_with_means_the_old_contact_text(self):
        with override_settings(RAZORPAY_KEY_ID=''):
            html = self.page().content.decode()
        self.assertNotIn('Pay now', html)

    def test_a_waiting_or_rejected_payment_is_explained(self):
        self.set_upi()
        self.order(status='waiting', utr='401234567890')
        self.order(status='rejected', utr='222222222222', note='Not found')
        html = self.page().content.decode()
        self.assertIn('waiting for approval', html)
        self.assertIn('401234567890', html)
        self.assertIn('Not found', html)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class PlatformPaymentsTests(PayTestBase):
    def test_the_page_lists_waiting_payments_with_the_number_to_check(self):
        self.order(status='waiting', utr='401234567890')
        r = self.client.get('/superadmin/payments/')
        self.assertContains(r, '401234567890')
        self.assertContains(r, 'Sharma Medical')
        self.assertContains(self.client.get('/superadmin/'), '1 UPI payment')

    def test_approve_extends_the_plan_and_cannot_be_done_twice(self):
        o = self.order(status='waiting', utr='401234567890')
        r = self.client.post(f'/superadmin/payments/{o.pk}/approve/')
        self.assertRedirects(r, '/superadmin/payments/', fetch_redirect_response=False)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))
        self.assertIn('approved by boss', self.shop.payments.get().note)
        self.client.post(f'/superadmin/payments/{o.pk}/approve/')
        self.assertEqual(self.shop.payments.count(), 1)

    def test_reject_with_and_without_a_reason(self):
        a, b = self.order(status='waiting', utr='111111111111'), self.order(status='waiting', utr='222222222222')
        self.client.post(f'/superadmin/payments/{a.pk}/reject/', dict(reason='Amount too low'))
        self.client.post(f'/superadmin/payments/{b.pk}/reject/')
        a.refresh_from_db(), b.refresh_from_db()
        self.assertEqual((a.status, a.note), ('rejected', 'Amount too low'))
        self.assertEqual(b.status, 'rejected')
        self.assertTrue(b.note)
        self.assertEqual(len(mail.outbox), 2)

    def test_only_a_waiting_order_can_be_approved(self):
        o = self.order(status='created')
        self.client.post(f'/superadmin/payments/{o.pk}/approve/')
        o.refresh_from_db()
        self.assertEqual(o.status, 'created')
        self.assertEqual(self.shop.payments.count(), 0)

    def test_others_cannot_see_or_use_the_pages(self):
        o = self.order(status='waiting', utr='401234567890')
        self.client.logout()
        self.assertEqual(self.client.get('/superadmin/payments/').status_code, 302)           # sign in first
        self.client.force_login(get_user_model().objects.create_user('clerk', 'c@example.com', 'pw-12345'))
        self.assertEqual(self.client.get('/superadmin/payments/').status_code, 404)
        self.assertEqual(self.client.post(f'/superadmin/payments/{o.pk}/approve/').status_code, 404)
        o.refresh_from_db()
        self.assertEqual(o.status, 'waiting')

    def test_get_does_not_approve(self):
        o = self.order(status='waiting')
        self.assertEqual(self.client.get(f'/superadmin/payments/{o.pk}/approve/').status_code, 405)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class MiddlewareAndSettingsTests(PayTestBase):
    def test_a_locked_shop_can_still_pay(self):
        for path in ('/plan/pay/billing/', '/plan/pay/autopay/', '/plan/pay/autopay/verify/', '/plan/pay/autopay/5/cancel/', '/plan/pay/upi/', '/plan/pay/upi/5/', '/plan/pay/5/cancel/'):
            self.assertTrue(middleware.is_always_open(path), path)
        self.assertFalse(middleware.is_always_open('/bills/new/'))

    def test_upi_settings_are_saved_and_read(self):
        self.assertIsNone(site.upi())
        site.save(dict(upi_id='medsync@okhdfcbank', upi_name=''))
        cache.clear()
        self.assertEqual(site.upi(), ('medsync@okhdfcbank', 'MedSync'))
        self.assertEqual(SiteSetting.objects.get(pk=1).upi_id, 'medsync@okhdfcbank')

    def test_the_settings_page_saves_the_upi_id_and_rejects_a_bad_one(self):
        ok = self.client.post('/superadmin/site/', dict(upi_id='medsync@okhdfcbank', upi_name='MedSync'))
        self.assertRedirects(ok, '/superadmin/site/', fetch_redirect_response=False)
        self.assertEqual(SiteSetting.objects.get(pk=1).upi_id, 'medsync@okhdfcbank')
        bad = self.client.post('/superadmin/site/', dict(upi_id='not a upi'))
        self.assertContains(bad, 'UPI ID looks wrong')
        page = self.client.get('/superadmin/site/').content.decode()
        self.assertIn('UPI ID for shops', page)
        self.assertIn('Autopay (Razorpay Subscriptions)', page)


class GstPureTests(SimpleTestCase):
    SELLER = '19AABCU9603R1ZK'

    def test_gstin_check(self):
        for good in (self.SELLER, '27AAPFU0939F1ZV', ' 19aabcu9603r1zk '):
            self.assertTrue(gst.gstin_ok(good), good)
        for bad in ('', '19AABCU9603R1ZJ', '99AABCU9603R1ZK', '19AABCU9603R1Y' + 'K', '19AABCU9603R1Z', '19AABCU9603R1ZKK', 'ABCDEFGHIJKLMNO'):
            self.assertFalse(gst.gstin_ok(bad), bad)
        self.assertEqual((gst.state_of(self.SELLER), gst.state_of('nonsense')), ('19', ''))

    def test_the_same_state_pays_cgst_and_sgst_in_equal_halves(self):
        for total in ('500', '5000', '1', '99', '777.77', '12345.67'):
            p = gst.tax_for(total, self.SELLER, 'IN', '19')
            self.assertEqual(p['kind'], 'cgst_sgst')
            self.assertEqual(p['cgst'], p['sgst'])
            self.assertEqual(p['taxable'] + p['cgst'] + p['sgst'], Decimal(total))
            self.assertEqual(p['igst'], 0)
        p = gst.tax_for(500, self.SELLER, 'IN', '19')
        self.assertEqual((p['taxable'], p['cgst'], p['sgst']), (Decimal('423.74'), Decimal('38.13'), Decimal('38.13')))

    def test_another_state_pays_igst(self):
        p = gst.tax_for(500, self.SELLER, 'IN', '27')
        self.assertEqual((p['kind'], p['taxable'], p['igst'], p['cgst'], p['sgst']), ('igst', Decimal('423.73'), Decimal('76.27'), 0, 0))
        self.assertEqual(p['taxable'] + p['igst'], 500)

    def test_another_country_pays_no_tax(self):
        for state in ('', '19', '27'):
            p = gst.tax_for(500, self.SELLER, 'US', state)
            self.assertEqual((p['kind'], p['taxable'], p['tax'], p['total']), ('export', Decimal('500.00'), 0, Decimal('500.00')))

    def test_a_seller_without_a_gst_number_charges_no_gst(self):
        for country in ('IN', 'US'):
            p = gst.tax_for(500, '', country, '19')
            self.assertEqual((p['kind'], p['taxable'], p['tax']), ('none', Decimal('500.00'), 0))

    def test_an_unknown_state_is_taken_as_the_sellers_state_by_the_caller_not_igst(self):
        self.assertEqual(gst.tax_for(500, self.SELLER, 'IN', '')['kind'], 'cgst_sgst')

    def test_the_lines_shown_to_the_customer(self):
        self.assertEqual(gst.rows_for(gst.tax_for(500, self.SELLER, 'IN', '19')),
                         [('Price without GST', 'Rs 423.74'), ('CGST (9%)', 'Rs 38.13'), ('SGST (9%)', 'Rs 38.13'), ('Total to pay now', 'Rs 500.00')])
        self.assertEqual(gst.rows_for(gst.tax_for(500, self.SELLER, 'IN', '27'))[1], ('IGST (18%)', 'Rs 76.27'))
        self.assertEqual(gst.rows_for(gst.tax_for(500, self.SELLER, 'DE', ''))[1], ('Tax (export of service, zero-rated)', 'Rs 0.00'))
        self.assertEqual(gst.rows_for(gst.tax_for(500, '', 'IN', '19')), [('Price', 'Rs 500.00'), ('Total to pay now', 'Rs 500.00')])

    def test_the_billing_form_is_checked(self):
        good = dict(bill_name=' Raj  Sharma ', bill_country='in', bill_state='19', bill_address='5 Park Street\r\n\r\n Kolkata ', bill_tax_id=' 19aabcu9603r1zk')
        values, errors = gst.clean_billing(good)
        self.assertEqual(errors, [])
        self.assertEqual(values, dict(bill_name='Raj Sharma', bill_country='IN', bill_state='19', bill_address='5 Park Street\nKolkata', bill_tax_id='19AABCU9603R1ZK'))
        for change, text in ((dict(bill_name=''), 'name'), (dict(bill_country='XX'), 'country'), (dict(bill_address=' '), 'address'), (dict(bill_state=''), 'state'),
                             (dict(bill_state='99'), 'state'), (dict(bill_tax_id='19AABCU9603R1ZJ'), 'GST number is not valid'),
                             (dict(bill_state='27'), 'different state')):
            _, errors = gst.clean_billing(dict(good, **change))
            self.assertTrue(any(text in e for e in errors), (change, errors))
        values, errors = gst.clean_billing(dict(good, bill_country='DE', bill_state='19', bill_tax_id='DE123456789'))      # abroad: no state, free tax number
        self.assertEqual((errors, values['bill_state'], values['bill_tax_id']), ([], '', 'DE123456789'))
        _, errors = gst.clean_billing(dict(good, bill_country='DE', bill_tax_id='X' * 31))
        self.assertTrue(errors)

    def test_the_financial_year_and_the_number_fit_gst_rules(self):
        from datetime import date
        if not HAS_TENANTS:
            self.skipTest('needs the tenants app')
        self.assertEqual([invoicing.financial_year(date(*d)) for d in ((2026, 3, 31), (2026, 4, 1), (2026, 12, 31), (2027, 3, 31), (2099, 4, 1))],
                         ['2025-26', '2026-27', '2026-27', '2026-27', '2099-00'])
        self.assertLessEqual(len('MS/2026-27/0001'), 16)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class InvoiceMakingTests(PayTestBase):
    def pay(self, plan='monthly', amount=Decimal('500'), today=None, shop=None):
        shop = shop or self.shop
        with transaction_atomic():
            payment = billing.record_payment(shop, plan, amount, today or self.today, 'test')
            shop.save()
        return payment.invoice

    def test_a_payment_in_the_sellers_state_makes_a_cgst_sgst_invoice(self):
        inv = self.pay()
        self.assertEqual(inv.number, f'MS/{invoicing.financial_year(self.today)}/0001')
        self.assertEqual((inv.kind, inv.rate, inv.taxable, inv.cgst, inv.sgst, inv.igst, inv.total),
                         ('cgst_sgst', Decimal('18'), Decimal('423.74'), Decimal('38.13'), Decimal('38.13'), 0, Decimal('500.00')))
        self.assertEqual((inv.seller_name, inv.seller_gstin, inv.sac, inv.seller_lut), ('Socialfly Test', '19AABCU9603R1ZK', '998314', 'AD190000000000A'))
        self.assertEqual((inv.buyer_name, inv.buyer_country, inv.buyer_state, inv.date, inv.plan), ('Sharma Medical', 'IN', '19', self.today, 'monthly'))
        self.assertEqual((inv.period_from, inv.period_to), (self.today, tenancy.add_months(self.today, 1)))
        self.assertEqual(inv.payment.invoice, inv)

    def test_another_state_gets_igst_and_a_gst_number_is_printed(self):
        Shop.objects.filter(pk=self.shop.pk).update(bill_state='27', bill_tax_id='27AAPFU0939F1ZV')
        self.shop.refresh_from_db()
        inv = self.pay('yearly', Decimal('5000'))
        self.assertEqual((inv.kind, inv.igst, inv.cgst, inv.sgst, inv.taxable, inv.buyer_tax_id), ('igst', Decimal('762.71'), 0, 0, Decimal('4237.29'), '27AAPFU0939F1ZV'))

    def test_another_country_gets_an_export_invoice_without_tax(self):
        Shop.objects.filter(pk=self.shop.pk).update(bill_country='DE', bill_state='', bill_tax_id='DE123456789')
        self.shop.refresh_from_db()
        inv = self.pay()
        self.assertEqual((inv.kind, inv.rate, inv.taxable, inv.igst, inv.total, inv.buyer_country), ('export', 0, Decimal('500.00'), 0, Decimal('500.00'), 'DE'))

    def test_a_seller_without_a_gst_number_makes_a_plain_invoice(self):
        site.save(dict(seller_gstin=''))
        inv = self.pay()
        self.assertEqual((inv.kind, inv.taxable, inv.cgst, inv.total), ('none', Decimal('500.00'), 0, Decimal('500.00')))

    def test_a_shop_that_never_gave_details_is_taken_as_indian_in_the_sellers_state(self):
        Shop.objects.filter(pk=self.shop.pk).update(bill_name='', bill_country='', bill_state='', bill_address='')
        self.shop.refresh_from_db()
        inv = self.pay()
        self.assertEqual((inv.kind, inv.buyer_country, inv.buyer_name), ('cgst_sgst', 'IN', 'Sharma Medical'))

    def test_numbers_run_one_after_another_and_start_again_each_financial_year(self):
        from datetime import date
        a = self.pay(today=date(2026, 3, 30))
        b = self.pay(today=date(2026, 3, 31))
        c = self.pay(today=date(2026, 4, 1))
        d = self.pay(today=date(2026, 4, 2))
        self.assertEqual([i.number for i in (a, b, c, d)], ['MS/2025-26/0001', 'MS/2025-26/0002', 'MS/2026-27/0001', 'MS/2026-27/0002'])
        self.assertEqual(len({i.number for i in Invoice.objects.all()}), 4)

    def test_a_payment_that_is_rolled_back_loses_no_number(self):
        try:
            with transaction_atomic():
                billing.record_payment(self.shop, 'monthly', Decimal('500'), self.today, 'x')
                raise RuntimeError('something failed after the invoice')
        except RuntimeError:
            pass
        self.assertEqual((Invoice.objects.count(), InvoiceCounter.objects.filter(last__gt=0).count()), (0, 0))
        self.assertTrue(self.pay().number.endswith('/0001'))

    def test_an_old_invoice_never_changes_when_details_change(self):
        inv = self.pay()
        site.save(dict(seller_name='Another Name', seller_gstin='27AAPFU0939F1ZV'))
        Shop.objects.filter(pk=self.shop.pk).update(bill_name='New Name', bill_country='US')
        inv.refresh_from_db()
        self.assertEqual((inv.seller_name, inv.seller_gstin, inv.buyer_name, inv.buyer_country, inv.kind), ('Socialfly Test', '19AABCU9603R1ZK', 'Sharma Medical', 'IN', 'cgst_sgst'))

    def test_every_way_of_paying_makes_one_invoice(self):
        o = self.order(status='waiting', utr='401234567890')
        billing.settle(o.pk, 'UPI approved', allowed=('waiting',))
        billing.settle(o.pk, 'again', allowed=('waiting',))                                      # a second approval does nothing
        self.assertEqual(Invoice.objects.count(), 1)
        from django.test import RequestFactory
        from tenants import platform
        req = RequestFactory().post('/x/', dict(action='paid', plan='monthly', amount='', note='cash'))
        platform._apply(req, self.shop, self.today)                                               # the platform owner's own payment button
        self.assertEqual(Invoice.objects.count(), 2)

    def test_the_receipt_mail_names_the_invoice(self):
        o = self.order(status='waiting', utr='401234567890')
        billing.settle(o.pk, 'UPI approved', allowed=('waiting',))
        inv = Invoice.objects.get()
        self.assertIn(inv.number, mail.outbox[0].body)
        self.assertIn('CGST (9%): Rs 38.13', mail.outbox[0].body)


def transaction_atomic():
    from django.db import transaction
    return transaction.atomic()


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class InvoiceViewTests(PayTestBase):
    def make(self, shop=None, **extra):
        shop = shop or self.shop
        for k, v in extra.items():
            setattr(shop, k, v)
        with transaction_atomic():
            payment = billing.record_payment(shop, 'monthly', Decimal('500'), self.today, '')
            shop.save()
        return payment.invoice

    def test_the_owner_opens_his_invoice_with_all_the_parts(self):
        inv = self.make()
        self.on_shop()
        html = self.client.get(f'/plan/invoices/{inv.pk}/').content.decode()
        for text in ('Tax Invoice', inv.number, 'Socialfly Test', 'GSTIN: 19AABCU9603R1ZK', 'Sharma Medical', '5 Park Street', 'West Bengal (19)', '998314',
                     '423.74', 'CGST 9%', 'SGST 9%', '38.13', '500.00', 'Print / Save as PDF'):
            self.assertIn(text, html)
        self.assertNotIn('IGST', html)

    def test_an_export_invoice_says_so_and_names_the_lut(self):
        inv = self.make(bill_country='DE', bill_state='', bill_tax_id='DE123456789')
        self.on_shop()
        html = self.client.get(f'/plan/invoices/{inv.pk}/').content.decode()
        for text in ('Germany', 'Tax no.: DE123456789', 'Export of services', 'zero-rated', 'AD190000000000A', 'Rs 0.00'):
            self.assertIn(text, html)
        self.assertNotIn('CGST', html)

    def test_an_invoice_without_gst_is_called_just_invoice(self):
        site.save(dict(seller_gstin=''))
        inv = self.make()
        self.on_shop()
        html = self.client.get(f'/plan/invoices/{inv.pk}/').content.decode()
        self.assertIn('<h1>Invoice</h1>', html)
        self.assertIn('not registered under GST', html)

    def test_a_shop_cannot_open_the_invoice_of_another_shop(self):
        other = Shop(schema_name='s_other_test', name='Other', email='o@example.com', plan='trial', bill_name='Other', bill_country='IN', bill_state='19', bill_address='x')
        other.auto_create_schema = False
        other.save()
        inv = self.make(shop=other)
        self.on_shop()
        self.assertEqual(self.client.get(f'/plan/invoices/{inv.pk}/').status_code, 404)

    def test_the_plan_page_lists_the_invoice_number_with_a_link(self):
        inv = self.make()
        self.on_shop()
        html = self.client.get('/plan/').content.decode()
        self.assertIn(inv.number, html)
        self.assertIn(f'/plan/invoices/{inv.pk}/', html)

    def test_others_cannot_open_an_invoice(self):
        inv = self.make()
        self.client.logout()
        self.assertEqual(self.client.get(f'/superadmin/invoices/{inv.pk}/').status_code, 302)
        self.client.force_login(get_user_model().objects.create_user('clerk', 'c@example.com', 'pw-12345'))
        self.assertEqual(self.client.get(f'/superadmin/invoices/{inv.pk}/').status_code, 404)
        self.assertEqual(self.client.get('/superadmin/invoices/').status_code, 404)
        self.assertEqual(self.client.get('/superadmin/invoices/csv/').status_code, 404)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**RAZ)
class BillingDetailsTests(PayTestBase):
    GOOD = dict(bill_name='Raj Sharma Medical', bill_country='IN', bill_state='07', bill_address='1 Main Road\nDelhi', bill_tax_id='')

    def test_the_details_are_saved(self):
        self.on_shop()
        r = self.client.post('/plan/pay/billing/', self.GOOD)
        self.assertRedirects(r, '/plan/', fetch_redirect_response=False)
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.bill_name, self.shop.bill_state, self.shop.bill_address), ('Raj Sharma Medical', '07', '1 Main Road\nDelhi'))

    def test_wrong_details_change_nothing_and_say_why(self):
        self.on_shop()
        r = self.client.post('/plan/pay/billing/', dict(self.GOOD, bill_state='', bill_tax_id='19AABCU9603R1ZJ'), follow=True)
        self.assertContains(r, 'Choose the state')
        self.assertContains(r, 'GST number is not valid')
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.bill_name, self.shop.bill_state), ('Sharma Medical', '19'))

    def test_a_foreign_customer_gives_a_country_and_no_state(self):
        self.on_shop()
        self.client.post('/plan/pay/billing/', dict(self.GOOD, bill_country='AE', bill_state='07', bill_tax_id='TRN 100200300'))
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.bill_country, self.shop.bill_state, self.shop.bill_tax_id), ('AE', '', 'TRN100200300'))

    def test_get_is_refused_and_it_is_for_the_owner_only(self):
        self.on_shop()
        self.assertEqual(self.client.get('/plan/pay/billing/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post('/plan/pay/billing/', self.GOOD).status_code, 302)

    def test_nobody_can_pay_before_the_details_are_given(self):
        self.set_upi()
        Shop.objects.filter(pk=self.shop.pk).update(bill_address='')
        self.shop.refresh_from_db()
        self.on_shop()
        with mock.patch('tenants.razorpay.check_plan'), mock.patch('tenants.razorpay.create_subscription', return_value='sub_1') as cs:
            r = self.client.post('/plan/pay/autopay/', dict(plan='monthly'))
        self.assertEqual((r.status_code, r.json()['ok']), (400, False))
        self.assertIn('invoice details', r.json()['error'])
        cs.assert_not_called()
        self.assertEqual(AutoPay.objects.count(), 0)
        r = self.client.post('/plan/pay/upi/', dict(plan='monthly'), follow=True)
        self.assertContains(r, 'save your invoice details first')
        self.assertEqual(PlanOrder.objects.count(), 0)

    def test_the_form_shows_the_saved_values_and_the_lists(self):
        self.on_shop()
        html = self.client.get('/plan/').content.decode()
        for text in ('name="bill_name" maxlength="120" value="Sharma Medical"', '<option value="IN" selected>India</option>', '<option value="19" selected>West Bengal</option>',
                     'Other country', 'CGST + SGST in West Bengal'):
            self.assertIn(text, html)
        self.assertNotIn('value="25"', html)                                  # the old Daman and Diu code is not offered
        self.assertLess(html.index('>India<'), html.index('>Germany<'))       # India first
        self.assertLess(html.index('>Germany<'), html.index('>Other country<'))


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class PlatformInvoiceListTests(PayTestBase):
    def make(self, day, amount='500', **shop_fields):
        for k, v in shop_fields.items():
            setattr(self.shop, k, v)
        with transaction_atomic():
            payment = billing.record_payment(self.shop, 'monthly', Decimal(amount), day, '')
            self.shop.save()
        return payment.invoice

    def test_the_list_shows_one_month_with_totals_and_other_months_are_left_out(self):
        from datetime import date
        a = self.make(date(2026, 10, 5))
        b = self.make(date(2026, 10, 20), bill_state='27')
        self.make(date(2026, 11, 2))
        r = self.client.get('/superadmin/invoices/?month=2026-10')
        self.assertContains(r, a.number)
        self.assertContains(r, b.number)
        self.assertNotContains(r, 'MS/2026-27/0003')
        self.assertContains(r, 'October 2026 (2)')
        self.assertContains(r, '1000.00')                                      # total of the two
        self.assertContains(self.client.get('/superadmin/invoices/?month=2026-12'), 'No invoice in this month.')
        self.assertEqual(self.client.get('/superadmin/invoices/?month=bad').status_code, 200)

    def test_the_csv_has_one_line_per_invoice_for_the_return(self):
        from datetime import date
        self.make(date(2026, 10, 5))
        self.make(date(2026, 10, 20), bill_country='DE', bill_state='', bill_tax_id='DE1')
        r = self.client.get('/superadmin/invoices/csv/?month=2026-10')
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('invoices-2026-10.csv', r['Content-Disposition'])
        import csv
        import io
        rows = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:3], ['Invoice no', 'Date', 'Customer'])
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[1][0], rows[1][6], rows[1][7], rows[1][8], rows[1][9], rows[1][10], rows[1][11]), ('MS/2026-27/0001', 'CGST + SGST', '423.74', '38.13', '38.13', '0.00', '500.00'))
        self.assertEqual((rows[2][3], rows[2][4], rows[2][6], rows[2][8]), ('DE1', 'Germany', 'Export (zero-rated)', '0.00'))

    def test_the_menu_has_the_invoices_link(self):
        self.assertContains(self.client.get('/superadmin/invoices/'), 'href="/superadmin/invoices/"')

    def test_an_invoice_can_be_opened_from_the_list(self):
        inv = self.make(self.today)
        r = self.client.get(f'/superadmin/invoices/{inv.pk}/')
        self.assertContains(r, 'Tax Invoice')
        self.assertContains(r, 'Back to invoices')


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
class SellerSettingsTests(PayTestBase):
    GOOD = dict(seller_name='Socialfly', seller_address='127/3 College Street\nKolkata 700007', seller_gstin='19aabcu9603r1zk', seller_lut='', seller_sac='998314')

    def test_the_seller_details_are_saved_cleaned_and_shown_with_the_state(self):
        r = self.client.post('/superadmin/site/', self.GOOD)
        self.assertRedirects(r, '/superadmin/site/', fetch_redirect_response=False)
        row = SiteSetting.objects.get(pk=1)
        self.assertEqual((row.seller_name, row.seller_gstin, row.seller_sac, row.seller_address), ('Socialfly', '19AABCU9603R1ZK', '998314', '127/3 College Street\nKolkata 700007'))
        page = self.client.get('/superadmin/site/').content.decode()
        self.assertIn('West Bengal (19)' if False else 'State: <b>West Bengal</b>', page)
        self.assertIn('value="19AABCU9603R1ZK"', page)

    def test_a_wrong_gst_number_or_sac_is_refused_and_nothing_is_saved(self):
        before = SiteSetting.objects.get(pk=1).seller_gstin
        r = self.client.post('/superadmin/site/', dict(self.GOOD, seller_gstin='19AABCU9603R1ZJ', seller_sac='12'), follow=True)
        self.assertContains(r, 'Your GST number is not valid')
        self.assertContains(r, 'SAC code is 4 to 8 digits')
        self.assertEqual(SiteSetting.objects.get(pk=1).seller_gstin, before)

    def test_leaving_the_gst_number_blank_is_allowed(self):
        self.client.post('/superadmin/site/', dict(self.GOOD, seller_gstin=''))
        self.assertEqual(SiteSetting.objects.get(pk=1).seller_gstin, '')
