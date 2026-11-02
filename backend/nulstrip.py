"""Remove NUL (\\x00) characters from submitted text.

PostgreSQL refuses a NUL byte in any text value ("PostgreSQL text fields cannot contain NUL"),
so a crafted search such as ?q=%00 would end in a server error. SQLite accepts it, which hides the
problem during local tests. Cleaning the input once here covers every search box and form.
"""


def _clean(qd):
    if not any('\x00' in v for k in qd for v in qd.getlist(k)):
        return qd
    fresh = qd.copy()          # a mutable copy
    for k in list(fresh):
        fresh.setlist(k, [v.replace('\x00', '') for v in fresh.getlist(k)])
    return fresh


class StripNulMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.GET = _clean(request.GET)
        if request.method == 'POST' and (request.content_type or '') in (
                'application/x-www-form-urlencoded', 'multipart/form-data'):
            request.POST = _clean(request.POST)
        return self.get_response(request)
