"""Removes the invoices and payments that were made while testing, before the first real customer, and starts the invoice
numbers again from 0001.

    python manage.py clear_test_invoices --before 2026-11-02          # only shows what would go
    python manage.py clear_test_invoices --before 2026-11-02 --yes    # really removes it

Everything CREATED BEFORE the given day (or time: "2026-11-02 18:30", local time) is taken as test data: its invoices, payments,
payment orders and autopay records. Later ones are never touched, and neither are the shops themselves (a test shop keeps the
plan and the paid-until date that its test payment gave it; change those in the platform panel if you want).

Invoice numbers must not have gaps, so when a real invoice of the same financial year exists after the cut, nothing is removed
unless you add --allow-gaps (the numbers then go on after the highest invoice that stays)."""
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tenants.models import AutoPay, Invoice, InvoiceCounter, Payment, PlanOrder


def parse_cut(text):
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return timezone.make_aware(datetime.strptime(text.strip(), fmt))
        except ValueError:
            pass
    raise CommandError('--before must look like 2026-11-02 or "2026-11-02 18:30".')


def sequence(number):
    return int(number.rsplit('/', 1)[1])


class Command(BaseCommand):
    help = 'Removes test invoices/payments made before a day and restarts the invoice numbers (shows a preview unless --yes).'

    def add_arguments(self, parser):
        parser.add_argument('--before', required=True, help='Everything created before this day (2026-11-02) or time (2026-11-02 18:30) is test data.')
        parser.add_argument('--yes', action='store_true', help='Really remove it (without this only a preview is shown).')
        parser.add_argument('--allow-gaps', action='store_true', help='Remove even though a real invoice of the same financial year stays (its number is kept, so earlier numbers are missing).')

    def handle(self, *args, **opts):
        if not getattr(settings, 'MULTI_SHOP', False):
            raise CommandError('MULTI_SHOP is not switched on: there is nothing to clear.')
        cut = parse_cut(opts['before'])
        if cut > timezone.now():
            raise CommandError('--before is in the future: that would remove real invoices made today. Use a day that has already begun.')
        invoices = Invoice.objects.filter(created_at__lt=cut)
        payments = Payment.objects.filter(created_at__lt=cut)
        orders = PlanOrder.objects.filter(created_at__lt=cut)
        autopays = AutoPay.objects.filter(created_at__lt=cut)
        keep = Invoice.objects.filter(created_at__gte=cut)
        out = self.stdout.write
        out(f'Test data before {cut:%d %b %Y %H:%M}:')
        out(f'  invoices {invoices.count()}, payments {payments.count()}, payment orders {orders.count()}, autopay records {autopays.count()}')
        numbers = sorted(i.number for i in invoices)
        if numbers:
            out(f'  invoice numbers: {numbers[0]} ... {numbers[-1]}')
        out(f'Stays (made after that): invoices {keep.count()}, payments {Payment.objects.filter(created_at__gte=cut).count()}')
        shops = sorted({p.shop.name for p in payments.select_related('shop')})
        if shops:
            out('  shops that had test payments (not changed): ' + ', '.join(shops))
        active = autopays.filter(status='active')
        if active.exists():
            out(self.style.WARNING(f'  {active.count()} autopay record(s) are still active at Razorpay. Stop them in the Razorpay dashboard, or they keep charging.'))
        fys = {i.fy for i in invoices}
        clash = sorted({i.fy for i in keep} & fys)
        if clash and not opts['allow_gaps']:
            raise CommandError(
                f'A real invoice of the financial year {", ".join(clash)} was made after the cut, so removing the test invoices would leave gaps in the '
                'numbers. Nothing was removed. Choose a later --before only if that invoice is also a test, or add --allow-gaps if you accept the gap.')
        if not opts['yes']:
            out(self.style.NOTICE('This was only a preview. Add --yes to remove it.'))
            return
        with transaction.atomic():
            invoices.delete()
            payments.delete()
            orders.delete()
            autopays.delete()
            for counter in InvoiceCounter.objects.filter(fy__in=fys):
                left = [sequence(n) for n in Invoice.objects.filter(fy=counter.fy).values_list('number', flat=True)]
                if left:
                    counter.last = max(left)
                    counter.save(update_fields=['last'])
                else:
                    counter.delete()
        out(self.style.SUCCESS('Removed. The next invoice will be ' + self.next_number_text(fys)))

    def next_number_text(self, fys):
        parts = []
        for fy in sorted(fys):
            last = InvoiceCounter.objects.filter(fy=fy).values_list('last', flat=True).first() or 0
            parts.append(f'{getattr(settings, "INVOICE_PREFIX", "MS")}/{fy}/{last + 1:04d}')
        return ' / '.join(parts) or 'number 0001.'
