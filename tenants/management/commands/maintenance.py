"""Maintenance mode from the server (the deploy script uses it too).

    python manage.py maintenance status
    python manage.py maintenance on --minutes 20 --message "Adding new features"
    python manage.py maintenance schedule --start "2026-10-28 23:00" --minutes 30
    python manage.py maintenance extend --minutes 15
    python manage.py maintenance off          always works, also when the website is down

Times are in the shop time zone (Asia/Kolkata)."""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenants import maintenance


class Command(BaseCommand):
    help = 'Switch maintenance mode on or off, plan it, extend it, or show its state.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['status', 'on', 'off', 'schedule', 'extend'])
        parser.add_argument('--minutes', default='30', help='length in minutes (default 30; 15 for extend)')
        parser.add_argument('--message', default='', help='a short message for the shops')
        parser.add_argument('--start', default='', help='for schedule: "YYYY-MM-DD HH:MM"')

    def handle(self, *args, **o):
        action = o['action']
        if action == 'off':
            self.stdout.write('Maintenance is now off.' if maintenance.end_now() else 'Maintenance was already off.')
            return
        if action == 'status':
            kind, data = maintenance.state()
            if kind == 'off':
                self.stdout.write('Maintenance: off')
            else:
                self.stdout.write(f"Maintenance: {kind.upper()}, {maintenance.fmt(data['start'])} to {maintenance.fmt(data['end'])}")
            return
        minutes, problem = maintenance.clean_minutes(o['minutes'])
        if problem:
            raise CommandError(problem)
        message, problem = maintenance.clean_message(o['message'])
        if problem:
            raise CommandError(problem)
        if action == 'on':
            maintenance.start_now(minutes, message)
            kind, data = maintenance.state()
            self.stdout.write(self.style.SUCCESS(f"Maintenance is ON until {maintenance.fmt(data['end'])}."))
        elif action == 'extend':
            end = maintenance.extend(minutes)
            if end is None:
                raise CommandError('There is no maintenance to extend.')
            self.stdout.write(self.style.SUCCESS(f'Maintenance now ends at {maintenance.fmt(end)}.'))
        else:
            try:
                naive = datetime.strptime(o['start'].strip(), '%Y-%m-%d %H:%M')
            except ValueError:
                raise CommandError('Give the start as --start "YYYY-MM-DD HH:MM", for example --start "2026-10-28 23:00".')
            start = timezone.make_aware(naive, timezone.get_current_timezone())
            if start < timezone.now():
                raise CommandError('That start time is already over.')
            maintenance.schedule(start, start + timedelta(minutes=minutes), message)
            self.stdout.write(self.style.SUCCESS(f'Maintenance planned for {maintenance.fmt(start)}.'))
