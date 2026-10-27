"""The starting Units and Categories of a pharmacy. One list, used by a new shop, by the data reset and by `seed_master`."""
UNITS = ('Tablet', 'Capsule', 'Strip', 'Box', 'Bottle', 'ML', 'Tube', 'Vial', 'Sachet', 'Piece', 'Pack')
CATEGORIES = ('Tablet', 'Capsule', 'Syrup', 'Injection', 'Ointment / Cream', 'Drops', 'Powder / Sachet', 'Surgical',
              'Supplement', 'Personal Care', 'Other')


def add_missing():
    """Adds the units and categories that are not there yet (matched by name, ignoring capital letters).
    Never changes or removes anything. Returns (units added, categories added)."""
    from .models import Category, Unit
    out = []
    for model, names in ((Unit, UNITS), (Category, CATEGORIES)):
        have = {n.strip().lower() for n in model.objects.values_list('name', flat=True)}
        added = 0
        for name in names:
            if name.lower() not in have:
                model.objects.create(name=name)
                added += 1
        out.append(added)
    return tuple(out)
