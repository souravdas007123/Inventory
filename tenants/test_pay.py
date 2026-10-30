"""Paying for a plan: Razorpay (stubbed), UPI with QR + approval, the webhook and the platform's Payments page.

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
from tenants import razorpay, upi

HAS_TENANTS = 'tenants' in settings.INSTALLED_APPS
if HAS_TENANTS:
    from tenants import billing, middleware, payshop, site
    from tenants.models import PlanOrder, Shop, SiteSetting

KEY, SECRET, HOOK = 'rzp_test_key', 'key-secret-123', 'hook-secret-456'
RAZ = dict(RAZORPAY_KEY_ID=KEY, RAZORPAY_KEY_SECRET=SECRET, RAZORPAY_WEBHOOK_SECRET=HOOK, MULTI_SHOP=True)
HOST = dict(ALLOWED_HOSTS=['.localhost', 'testserver'], SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)


def sign(body, secret):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class RazorpayPureTests(SimpleTestCase):
    def test_checkout_signature(self):
        good = sign(b'order_1|pay_1', SECRET)
        self.assertTrue(razorpay.checkout_signature_ok('order_1', 'pay_1', good, SECRET))
        for args in (('order_2', 'pay_1', good), ('order_1', 'pay_2', good), ('order_1', 'pay_1', good[:-1] + '0'), ('order_1', 'pay_1', ''),
                     ('', 'pay_1', good), ('order_1', '', good)):
            self.assertFalse(razorpay.checkout_signature_ok(*args, secret=SECRET), args)
        self.assertFalse(razorpay.checkout_signature_ok('order_1', 'pay_1', good, ''))          # no secret, never valid

    def test_webhook_signature(self):
        body = b'{"event":"payment.captured"}'
        self.assertTrue(razorpay.webhook_signature_ok(body, sign(body, HOOK), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body + b' ', sign(body, HOOK), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, sign(body, 'other'), HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, '', HOOK))
        self.assertFalse(razorpay.webhook_signature_ok(body, sign(body, HOOK), ''))

    def test_paise(self):
        self.assertEqual((razorpay.paise(Decimal('500')), razorpay.paise('5000.00'), razorpay.paise(Decimal('499.5'))), (50000, 500000, 49950))

    @override_settings(**RAZ)
    def test_create_order_sends_the_right_request(self):
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"id": "order_ABC"}'

        def fake(req, timeout):
            seen.update(url=req.full_url, body=json.loads(req.data), auth=req.get_header('Authorization'))
            return Resp()
        with mock.patch('urllib.request.urlopen', fake):
            self.assertEqual(razorpay.create_order('MSREF', Decimal('500'), dict(shop='s_x')), 'order_ABC')
        self.assertEqual(seen['url'], 'https://api.razorpay.com/v1/orders')
        self.assertEqual((seen['body']['amount'], seen['body']['currency'], seen['body']['payment_capture'], seen['body']['receipt']),
                         (50000, 'INR', 1, 'MSREF'))
        import base64
        self.assertEqual(base64.b64decode(seen['auth'].split()[1]).decode(), f'{KEY}:{SECRET}')

    @override_settings(**RAZ)
    def test_create_order_failures_are_polite(self):
        import urllib.error
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('down')):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.create_order('R', 500)

        class Empty:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{}'
        with mock.patch('urllib.request.urlopen', return_value=Empty()):
            with self.assertRaises(razorpay.RazorpayError):
                razorpay.create_order('R', 500)

    def test_configured_needs_both_keys_and_many_shops(self):
        for keys, expect in ((dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True), True),
                             (dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='', MULTI_SHOP=True), False),
                             (dict(RAZORPAY_KEY_ID='', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=True), False),
                             (dict(RAZORPAY_KEY_ID='a', RAZORPAY_KEY_SECRET='b', MULTI_SHOP=False), False)):
            with override_settings(**keys):
                self.assertEqual(razorpay.configured(), expect, keys)


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
                         plan='trial', trial_ends=today + timedelta(days=3), email_verified=True)
        self.shop.auto_create_schema = False                      # a plain row: no schema is needed for these tests
        self.shop.save()
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
@override_settings(**RAZ)
class OnlinePayTests(PayTestBase):
    def start(self, plan='monthly'):
        self.on_shop()
        with mock.patch('tenants.razorpay.create_order', return_value='order_XYZ') as co:
            r = self.client.post('/plan/pay/online/', dict(plan=plan))
        return r, co

    def test_start_makes_an_order_at_the_server_price(self):
        r, co = self.start('yearly')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        o = PlanOrder.objects.get()
        self.assertEqual((data['ok'], data['key'], data['order_id'], data['amount'], data['currency']), (True, KEY, 'order_XYZ', 500000, 'INR'))
        self.assertEqual((o.plan, o.amount, o.method, o.status, o.razorpay_order_id), ('yearly', Decimal('5000'), 'razorpay', 'created', 'order_XYZ'))
        self.assertEqual(co.call_args.args[1], Decimal('5000'))
        self.assertNotIn(SECRET, r.content.decode())                           # the secret never leaves the server

    def test_the_browser_cannot_choose_the_amount(self):
        self.on_shop()
        with mock.patch('tenants.razorpay.create_order', return_value='order_1'):
            r = self.client.post('/plan/pay/online/', dict(plan='monthly', amount='1'))
        self.assertEqual(PlanOrder.objects.get().amount, Decimal('500'))
        self.assertEqual(r.json()['amount'], 50000)

    def test_a_wrong_plan_and_a_failing_service(self):
        self.on_shop()
        self.assertEqual(self.client.post('/plan/pay/online/', dict(plan='weekly')).status_code, 400)
        with mock.patch('tenants.razorpay.create_order', side_effect=razorpay.RazorpayError('down')):
            r = self.client.post('/plan/pay/online/', dict(plan='monthly'))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(PlanOrder.objects.get().status, 'cancelled')

    def test_too_many_attempts_are_stopped(self):
        for _ in range(payshop.MAX_ORDERS_PER_DAY):
            self.order(method='razorpay')
        r, _ = self.start()
        self.assertEqual(r.status_code, 429)

    def test_not_configured_means_404_and_get_is_refused(self):
        self.on_shop()
        with override_settings(RAZORPAY_KEY_ID=''):
            self.assertEqual(self.client.post('/plan/pay/online/', dict(plan='monthly')).status_code, 404)
        self.assertEqual(self.client.get('/plan/pay/online/').status_code, 405)

    def test_a_real_signature_extends_the_plan(self):
        self.start()
        sig = sign(b'order_XYZ|pay_777', SECRET)
        r = self.client.post('/plan/pay/online/verify/', dict(razorpay_order_id='order_XYZ', razorpay_payment_id='pay_777', razorpay_signature=sig))
        self.assertEqual((r.status_code, r.json()), (200, {'ok': True}))
        o = PlanOrder.objects.get()
        self.assertEqual((o.status, o.razorpay_payment_id), ('paid', 'pay_777'))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_forged_or_foreign_confirmation_changes_nothing(self):
        self.start()
        for oid, pid, sig in (('order_XYZ', 'pay_1', 'deadbeef'), ('order_XYZ', 'pay_1', sign(b'order_XYZ|pay_2', SECRET)),
                              ('order_NOPE', 'pay_1', sign(b'order_NOPE|pay_1', SECRET)), ('', '', '')):
            r = self.client.post('/plan/pay/online/verify/', dict(razorpay_order_id=oid, razorpay_payment_id=pid, razorpay_signature=sig))
            self.assertEqual(r.status_code, 400, (oid, pid))
        self.assertEqual(PlanOrder.objects.get().status, 'created')
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.paid_until)

    def test_another_shops_order_cannot_be_confirmed(self):
        other = Shop(schema_name='s_other_test', name='Other', email='o@example.com', plan='trial')
        other.auto_create_schema = False
        other.save()
        PlanOrder.objects.create(shop=other, plan='monthly', amount=Decimal('500'), method='razorpay', reference='MSOTHER111',
                                 razorpay_order_id='order_OTHER')
        self.on_shop()
        r = self.client.post('/plan/pay/online/verify/', dict(razorpay_order_id='order_OTHER', razorpay_payment_id='pay_1',
                                                              razorpay_signature=sign(b'order_OTHER|pay_1', SECRET)))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PlanOrder.objects.get(reference='MSOTHER111').status, 'created')

    def test_confirming_twice_never_extends_twice(self):
        self.start()
        data = dict(razorpay_order_id='order_XYZ', razorpay_payment_id='pay_777', razorpay_signature=sign(b'order_XYZ|pay_777', SECRET))
        self.assertTrue(self.client.post('/plan/pay/online/verify/', data).json()['ok'])
        self.assertTrue(self.client.post('/plan/pay/online/verify/', data).json()['ok'])
        self.assertEqual(self.shop.payments.count(), 1)


@skipUnless(HAS_TENANTS, 'needs the tenants app (many shops on)')
@override_settings(**RAZ)
class WebhookTests(PayTestBase):
    def event(self, name='payment.captured', order_id='order_XYZ', amount=50000, pay_id='pay_9'):
        return json.dumps(dict(event=name, payload=dict(payment=dict(entity=dict(id=pay_id, order_id=order_id, amount=amount, status='captured'))))).encode()

    def post(self, body, secret=HOOK, **extra):
        return self.client.post('/razorpay/webhook/', body, content_type='application/json',
                                HTTP_X_RAZORPAY_SIGNATURE=sign(body, secret) if secret else '', **extra)

    def setUp(self):
        super().setUp()
        self.client.logout()                                    # Razorpay is not signed in
        self.o = self.order(method='razorpay', razorpay_order_id='order_XYZ')

    def test_a_signed_captured_event_extends_the_plan_once(self):
        r = self.post(self.event())
        self.assertEqual((r.status_code, r.content), (200, b'ok'))
        self.o.refresh_from_db()
        self.assertEqual((self.o.status, self.o.razorpay_payment_id), ('paid', 'pay_9'))
        self.post(self.event())                                  # Razorpay sends it again
        self.assertEqual(self.shop.payments.count(), 1)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.paid_until, tenancy.add_months(self.today, 1))

    def test_order_paid_event_works_too(self):
        body = json.dumps(dict(event='order.paid', payload=dict(order=dict(entity=dict(id='order_XYZ', amount_paid=50000)),
                                                                 payment=dict(entity=dict(id='pay_3', order_id='order_XYZ', amount=50000))))).encode()
        self.assertEqual(self.post(body).content, b'ok')
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, 'paid')

    def test_a_bad_or_missing_signature_is_refused(self):
        body = self.event()
        self.assertEqual(self.post(body, secret='wrong').status_code, 400)
        self.assertEqual(self.post(body, secret='').status_code, 400)
        self.assertEqual(self.client.post('/razorpay/webhook/', body, content_type='application/json').status_code, 400)
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, 'created')

    def test_a_wrong_amount_unknown_order_and_other_events_change_nothing(self):
        self.assertEqual(self.post(self.event(amount=100)).content, b'amount differs')
        self.assertEqual(self.post(self.event(order_id='order_NOPE')).content, b'unknown order')
        self.assertEqual(self.post(self.event(name='payment.failed')).content, b'ignored')
        self.assertEqual(self.post(b'[1,2]').status_code, 200)
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, 'created')
        self.assertEqual(self.shop.payments.count(), 0)

    def test_garbage_that_is_signed_is_a_400(self):
        self.assertEqual(self.post(b'not json').status_code, 400)

    def test_without_a_webhook_secret_the_page_does_not_exist(self):
        with override_settings(RAZORPAY_WEBHOOK_SECRET=''):
            self.assertEqual(self.post(self.event()).status_code, 404)

    def test_a_cancelled_order_that_was_really_paid_is_still_credited(self):
        PlanOrder.objects.filter(pk=self.o.pk).update(status='cancelled')
        self.post(self.event())
        self.o.refresh_from_db()
        self.assertEqual(self.o.status, 'paid')

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get('/razorpay/webhook/').status_code, 405)


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
        self.assertIn('data-pay-online="monthly"', html)
        self.assertNotIn('/plan/pay/upi/', html)
        self.assertIn('https://checkout.razorpay.com/v1/checkout.js', html)         # loaded only when the button is pressed
        self.assertIn('GST', html)
        self.set_upi()
        html = self.page().content.decode()
        self.assertIn('action="/plan/pay/upi/"', html)
        with override_settings(RAZORPAY_KEY_ID=''):
            html = self.page().content.decode()
        self.assertNotIn('data-pay-online', html)
        self.assertIn('Pay by UPI', html)

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
        for path in ('/plan/pay/online/', '/plan/pay/online/verify/', '/plan/pay/upi/', '/plan/pay/upi/5/', '/plan/pay/5/cancel/'):
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
        self.assertIn('Online payment (Razorpay)', page)
