"""Month lock: the owner closes the books up to a date (Shop Settings). Bills and purchases dated on or before that
date can no longer be added, edited, cancelled or deleted, so the figures given to the CA / filed in GST stay fixed.
The owner can move the date back to reopen a period. Nothing is checked while no date is set."""
from django.core.exceptions import ValidationError


def closed_until():
    from .models import ShopProfile          # imported here: models imports this module
    return ShopProfile.objects.values_list('books_closed_until', flat=True).first()


def message(what, day, until):
    return (f"{what} dated {day:%d %b %Y} is in a closed period (the books are closed up to {until:%d %b %Y}), "
            f"so it cannot be changed. The owner can reopen the period in Shop Settings.")


def problem(what, *days):
    """'' when every date is open, otherwise a message. Blank dates are ignored."""
    until = closed_until()
    if not until:
        return ''
    for day in days:
        if day and day <= until:
            return message(what, day, until)
    return ''


def check(what, *days):
    """Raises ValidationError when one of the dates is in a closed period."""
    msg = problem(what, *days)
    if msg:
        raise ValidationError(msg)
