"""Owner data export: one zip file with a CSV for every table of the shop (products, bills, purchases, ...).

Values that start with = + - @ are prefixed with an apostrophe so Excel never runs them as a formula."""
import csv
import io
import zipfile

from django.apps import apps
from django.utils import timezone

SKIP = {'BlogPost', 'ImportLayout', 'AuditLog', 'LoginAudit'}
DANGEROUS = ('=', '+', '-', '@', '\t', '\r')


def safe(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    text = str(value)
    if text.startswith(DANGEROUS) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def models():
    return [m for m in apps.get_app_config('backend').get_models() if m.__name__ not in SKIP]


def table_csv(model):
    fields = [f for f in model._meta.concrete_fields]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([f.name for f in fields])
    count = 0
    for row in model.objects.all().order_by('pk').values_list(*[f.attname for f in fields]).iterator(chunk_size=2000):
        writer.writerow([safe(v) for v in row])
        count += 1
    return '﻿' + out.getvalue(), count        # BOM so Excel opens non-English text correctly


def build_zip(shop_name=''):
    buf = io.BytesIO()
    lines = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for model in models():
            text, count = table_csv(model)
            name = model._meta.model_name + '.csv'
            z.writestr(name, text.encode('utf-8'))
            lines.append(f'{name}: {count} rows')
        readme = (f'MedSync data export{" for " + shop_name if shop_name else ""}\n'
                  f'Created: {timezone.localtime():%d %b %Y, %I:%M %p}\n\n'
                  'Each file is one table and opens in Excel or Google Sheets. Relations are shown as ID numbers.\n\n'
                  + '\n'.join(lines) + '\n')
        z.writestr('README.txt', readme)
    return buf.getvalue()
