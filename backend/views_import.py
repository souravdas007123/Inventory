"""Bulk import / export screens: /import/ and /import/<kind>/"""
import json

from django.contrib import messages
from django.http import Http404
from django.utils import timezone

from . import access, auditlog, imports
from .imports import IMPORTERS
from .models import ImportLayout
from .tabular import ImportFileError, read_table, txt
from .views import page

SHOW_ROWS = 200


# ------------------------------------------------------------------
# Column matching: a file whose headers are not ours (e.g. a supplier's own export) is matched once and remembered
# ------------------------------------------------------------------

def _saved_layout(kind, headers):
    row = ImportLayout.objects.filter(kind=kind, signature=imports.layout_signature(headers)).first()
    if not row:
        return None
    try:
        data = json.loads(row.mapping)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data else None


def _load_raw(request, kind, token):
    payload = imports.load_stash(token)
    if not payload or payload.get('stage') != 'raw' or payload.get('kind') != kind or payload.get('user') != request.user.pk:
        return None
    return payload


def _show_match(ctx, imp, payload, token, current, missing, remember=True):
    headers, rows = payload['headers'], payload['rows']
    items = []
    for i, h in enumerate(headers):
        if not h:
            continue
        sample = next((txt(r[1].get(h)) for r in rows[:8] if txt(r[1].get(h))), '')
        items.append(dict(i=i, header=h, sample=sample[:40], selected=current.get(h, '')))
    ctx.update(stage='map', raw_token=token, map_items=items, missing=missing, remember=remember,
               filename=payload.get('filename', ''))


def _preview_or_match(ctx, imp, kind, payload, token, extra, saved=False):
    """Preview when every required column is found; otherwise the "Match columns" screen."""
    headers, rows, opts = payload['headers'], payload['rows'], payload['opts']
    mapped, missing, ignored = imports.prepare_rows(imp, headers, rows, extra)
    if missing:
        _show_match(ctx, imp, payload, token, imports.canonical_columns(imp, headers, extra), missing)
        return
    results = imp['validate'](mapped, opts)
    counts = {a: sum(1 for r in results if r['action'] == a) for a in ('create', 'update', 'skip', 'error')}
    done = imports.stash(dict(kind=kind, user=payload['user'], opts=opts, results=results, filename=payload.get('filename', '')))
    shown = sorted(results, key=lambda r: (r['action'] != 'error', r['n']))[:SHOW_ROWS]
    ctx.update(stage='preview', token=done, counts=counts, rows=shown, total=len(results),
               ok_count=counts['create'] + counts['update'], ignored=ignored, raw_token=token, layout_used=saved,
               filename=payload.get('filename', ''), truncated=len(results) > SHOW_ROWS)


def _match_columns(request, ctx, imp, kind):
    token = request.POST.get('raw_token', '')
    payload = _load_raw(request, kind, token)
    if not payload:
        messages.error(request, 'This file has expired or was not found. Please upload it again.')
        return
    headers = payload['headers']
    names = {c['name'] for c in imp['cols']}
    if request.POST.get('action') == 'remap':          # "Match columns" button on the preview
        _show_match(ctx, imp, payload, token, imports.canonical_columns(imp, headers, _saved_layout(kind, headers)), [])
        return
    mapping, used = {}, {}
    for i, h in enumerate(headers):
        if not h:
            continue
        choice = request.POST.get(f'm{i}', '')
        choice = choice if choice in names else ''
        mapping[h] = choice
        if choice:
            used.setdefault(choice, []).append(h)
    twice = [f"'{c}' (columns {', '.join(hs)})" for c, hs in used.items() if len(hs) > 1]
    remember = bool(request.POST.get('remember'))
    if twice:
        messages.error(request, 'One of our columns can be matched with only one column of the file: ' + '; '.join(twice))
        _show_match(ctx, imp, payload, token, mapping, [], remember)
        return
    _, missing, _ = imports.prepare_rows(imp, headers, payload['rows'], mapping)
    if missing:
        messages.error(request, 'These required columns are still not matched: ' + ', '.join(missing))
        _show_match(ctx, imp, payload, token, mapping, missing, remember)
        return
    if remember:
        ImportLayout.objects.update_or_create(
            kind=kind, signature=imports.layout_signature(headers),
            defaults=dict(mapping=json.dumps(mapping), headers=', '.join(h for h in headers if h)[:500]))
    _preview_or_match(ctx, imp, kind, payload, token, mapping, saved=False)


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
        elif request.POST.get('action') in ('map', 'remap'):
            try:
                _match_columns(request, ctx, imp, kind)
            except ImportFileError as e:
                messages.error(request, str(e))
        else:
            upload = request.FILES.get('file')
            if not upload:
                messages.error(request, 'Please choose a file first.')
            else:
                try:
                    headers, rows = read_table(upload, max_rows=imp['max_rows'])
                    opts = {'update': bool(request.POST.get('update')),
                            'create_products': bool(request.POST.get('create_products'))}
                    payload = dict(kind=kind, user=request.user.pk, stage='raw', headers=headers, rows=rows,
                                   filename=upload.name, opts=opts)
                    token = imports.stash(payload)
                    extra = _saved_layout(kind, headers)
                    _preview_or_match(ctx, imp, kind, payload, token, extra, saved=bool(extra))
                except ImportFileError as e:
                    messages.error(request, str(e))
    return page(request, 'app/import.html', **ctx)
