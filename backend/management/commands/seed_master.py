from django.core.management.base import BaseCommand

from backend.models import Category, Unit

UNITS = ['Tablet', 'Capsule', 'Strip', 'Box', 'Bottle', 'ML', 'Tube', 'Vial', 'Sachet', 'Piece', 'Pack']
CATEGORIES = ['Tablet', 'Capsule', 'Syrup', 'Injection', 'Ointment / Cream', 'Drops',
              'Powder / Sachet', 'Surgical', 'Supplement', 'Personal Care', 'Other']


class Command(BaseCommand):
    help = "Common Units aur Categories add karta hai (dobara chalane par duplicate nahi banata)."

    def handle(self, *args, **options):
        for model, names in ((Unit, UNITS), (Category, CATEGORIES)):
            added = 0
            for name in names:
                if not model.objects.filter(name=name).exists():
                    model.objects.create(name=name)
                    added += 1
            self.stdout.write(self.style.SUCCESS(f"{model.__name__}: {added} naye add hue"))