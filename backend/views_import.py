"""Bulk import / export screens: /import/ and /import/<kind>/"""
from django.contrib import messages
from django.http import Http404
from django.utils import timezone

from . import access, auditlog, imports
from .imports import IMPORTERS
from .tabular import ImportFileError, read_table
from .views import page

SHOW_ROWS = 200


@access.owner_required
def import_home(request):
    return page(request, 'app/import_home.html', active='import',
                items=[dict(key=k, can_export=bool(imp['export']), **{kk: v for kk, v in imp.items() if kk in ('title', 'icon', 'desc')})
                       for k, imp in IMPORTERS.items()])


@access.owner_required
def import_view(request, kind):
    imp = IMPORTERS.get(kind)
    if not imp:
        raise Http404

    if request.GET.get('template'):
        return imports.template_download(kind)
    if request.GET.get('export') and imp['export']:
        heads, rows = imp['export']()
        required = {c['name'] for c in imp['cols'] if c['required']}
        return imports.table_download(f"medsync_{kind}_{timezone.localdate():%Y%m%d}", heads, rows,
                                      request.GET.get('export'), required)

    # NB: callables (export/validate...) are auto-called in templates, so only use plain values from imp
    ctx = dict(active='import', kind=kind, imp=imp, cols=imp['cols'], stage='upload', can_export=bool(imp['export']))

    if request.method == 'POST':
        if request.POST.get('action') == 'commit':
            payload = imports.load_stash(request.POST.get('token', ''))
            if not payload or payload.get('kind') != kind or payload.get('user') != request.user.pk:
                messages.error(request, 'This preview has expired or was not found. Please upload the file again.')
            else:
                imports.drop_stash(request.POST.get('token'))     # double-click se double import na ho
                summary = imp['commit'](payload['results'], payload['opts'])
                auditlog.record(request, 'IMPORT', imp['title'],
                                f"{payload.get('filename', '')}: {summary['created']} created, {summary['updated']} updated, {len(summary['failed'])} failed")
                ctx.update(stage='done', summary=summary, filename=payload.get('filename', ''))
        else:
            upload = request.FILES.get('file')
            if not upload:
                messages.error(request, 'Please choose a file first.')
            else:
                try:
                    headers, rows = read_table(upload, max_rows=imp['max_rows'])
                    mapped, missing, ignored = imports.prepare_rows(imp, headers, rows)
                    if missing:
                        raise ImportFileError('The file is missing these required columns: ' + ', '.join(missing)
                                              + '. Download the template and use its headers.')
                    opts = {'update': bool(request.POST.get('update')),
                            'create_products': bool(request.POST.get('create_products'))}
                    results = imp['validate'](mapped, opts)
                except ImportFileError as e:
                    messages.error(request, str(e))
                else:
                    counts = {a: sum(1 for r in results if r['action'] == a) for a in ('create', 'update', 'skip', 'error')}
                    token = imports.stash(dict(kind=kind, user=request.user.pk, opts=opts, results=results,
                                               filename=upload.name))
                    shown = sorted(results, key=lambda r: (r['action'] != 'error', r['n']))[:SHOW_ROWS]
                    ctx.update(stage='preview', token=token, counts=counts, rows=shown, total=len(results),
                               ok_count=counts['create'] + counts['update'], ignored=ignored,
                               filename=upload.name, truncated=len(results) > SHOW_ROWS)
    return page(request, 'app/import.html', **ctx)
