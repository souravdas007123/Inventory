"""
Refill reminders: customers who buy the same medicine regularly (BP, sugar, thyroid ...) and are about to run out.

Django-free so it can be tested on its own. Nothing is stored: a customer simply drops off the list when they buy again
(or when they have been late for more than `grace` days, so old customers do not pile up).

A row is a dict: customer_id, product_id, date (the bill date).
"""
from datetime import timedelta
from statistics import median

MIN_BUYS = 2          # bought on at least 2 different days -> a pattern
MIN_GAP = 10          # a usual gap shorter than this is a one-off repeat, not a monthly refill
MAX_GAP = 75          # longer than this is occasional buying, not a regular medicine
LOOKAHEAD = 3         # remind this many days before the medicine is expected to finish
GRACE = 30            # keep a late customer on the list for this many days
LAST_GAPS = 3         # the usual gap = median of the latest few gaps


def usual_gap(dates):
    """Days between purchases (median of the latest gaps), or None when there is no regular pattern."""
    days = sorted(set(dates))
    if len(days) < MIN_BUYS:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])][-LAST_GAPS:]
    gap = median(gaps)
    return int(round(gap)) if MIN_GAP <= gap <= MAX_GAP else None


def due_refills(rows, today, lookahead=LOOKAHEAD, grace=GRACE):
    """Returns [{customer_id, items: [{product_id, last, expected, late}]}], most overdue customer first.
    `late` = days past the expected finish date (negative = still to come)."""
    by_pair = {}
    for r in rows:
        if r.get('customer_id') and r.get('product_id') and r.get('date'):
            by_pair.setdefault((r['customer_id'], r['product_id']), []).append(r['date'])
    by_customer = {}
    for (cid, pid), dates in by_pair.items():
        gap = usual_gap(dates)
        if not gap:
            continue
        last = max(dates)
        expected = last + timedelta(days=gap)
        late = (today - expected).days
        if -lookahead <= late <= grace:
            by_customer.setdefault(cid, []).append(dict(product_id=pid, last=last, expected=expected, late=late, gap=gap))
    out = [dict(customer_id=cid, items=sorted(items, key=lambda i: -i['late']),
                late=max(i['late'] for i in items)) for cid, items in by_customer.items()]
    out.sort(key=lambda c: (-c['late'], c['customer_id']))
    return out
