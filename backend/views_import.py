"""Bulk import / export screens: /import/ aur /import/<kind>/"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.utils import timezone

from . import auditlog, imports
from .imports import IMPORTERS
from .tabular import ImportFileError, read_table
from .views import page

SHOW_ROWS = 200


@login_required
def import_home(request):
    return page(request, 'app/import_home.html', active='import',
                items=[dict(key=k, can_export=bool(imp['export']), **{kk: v for kk, v in imp.items() if kk in ('title', 'icon', 'desc')})
                       for k, imp in IMPORTERS.items()])


@login_required
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

    # NB: template me callable (export/validate...) auto-call ho jata hai, isliye imp me se sirf plain values hi use karein
    ctx = dict(active='import', kind=kind, imp=imp, cols=imp['cols'], stage='upload', can_export=bool(imp['export']))

    if request.method == 'POST':
        if request.POST.get('action') == 'commit':
            payload = imports.load_stash(request.POST.get('token', ''))
            if not payload or payload.get('kind') != kind or payload.get('user') != request.user.pk:
                messages.error(request, 'Ye preview purana ho gaya hai ya mila nahi. File dobara upload karein.')
            else:
                imports.drop_stash(request.POST.get('token'))     # double-click se double import na ho
                summary = imp['commit'](payload['results'], payload['opts'])
                auditlog.record(request, 'IMPORT', imp['title'],
                                f"{payload.get('filename', '')}: {summary['created']} naye, {summary['updated']} update, {len(summary['failed'])} fail")
                ctx.update(stage='done', summary=summary, filename=payload.get('filename', ''))
        else:
            upload = request.FILES.get('file')
            if not upload:
                messages.error(request, 'Pehle koi file chuniye.')
            else:
                try:
                    headers, rows = read_table(upload, max_rows=imp['max_rows'])
                    mapped, missing, ignored = imports.prepare_rows(imp, headers, rows)
                    if missing:
                        raise ImportFileError('File me ye zaroori columns nahi mile: ' + ', '.join(missing)
                                              + '. Template download karke uske header use karein.')
                    opts = {'update': bool(request.POST.get('update'))}
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
