"""What the owner edits in the admin for the public website: the FAQ, the three policy pages and the main home page texts.

Every function here never raises. When the database cannot be read (or has nothing yet) the standard text from
site_seed.py / the template is used, so the public pages cannot be broken by an edit or by a missing table.
"""
import json
import logging

from django.conf import settings
from django.utils.safestring import mark_safe

from . import site_seed

log = logging.getLogger(__name__)


def json_for_script(data):
    """JSON for a <script type="application/ld+json"> tag. '<' is escaped so the text can never close the tag."""
    return mark_safe(json.dumps(data, ensure_ascii=False).replace('<', '\\u003c'))


def faqs():
    """[{'question', 'answer'}] in the owner's order. No rows at all (or an unreadable table): the standard FAQ.
    Rows that all are hidden: an empty list (the owner chose to show none)."""
    try:
        from .models import FaqItem
        rows = list(FaqItem.objects.all())
        if rows:
            return [dict(question=r.question, answer=r.answer) for r in rows if r.active]
    except Exception:
        log.exception('Could not read the FAQ')
    return [dict(question=q, answer=a) for q, a in site_seed.FAQS]


def faq_jsonld(items):
    """The FAQ markup Google reads (the same questions and answers as on the page); '' when there are none."""
    if not items:
        return ''
    return json_for_script([{'@type': 'Question', 'name': i['question'],
                             'acceptedAnswer': {'@type': 'Answer', 'text': i['answer']}} for i in items])


def home_text():
    """The edited home page texts as a dict; a blank value means 'use the standard text'."""
    out = dict(hero_title='', hero_lead='', final_title='', final_text='')
    try:
        from .models import HomeText
        row = HomeText.objects.first()
        if row:
            out = dict(hero_title=row.hero_title.strip(), hero_lead=row.hero_lead.strip(), final_title=row.final_title.strip(),
                       final_text=row.final_text.strip().replace('{days}', str(getattr(settings, 'TRIAL_DAYS', 14))))
    except Exception:
        log.exception('Could not read the home page text')
    return out


def legal_page(key):
    """(description, body_text, updated_date) of a policy page from the database, or None to use the built-in page."""
    try:
        from .models import LegalPage
        row = LegalPage.objects.filter(key=key).first()
        if row and row.body.strip():
            return row.description.strip() or site_seed.LEGAL_DESCRIPTIONS.get(key, ''), row.body, row.updated_on
    except Exception:
        log.exception('Could not read the policy page')
    return None
