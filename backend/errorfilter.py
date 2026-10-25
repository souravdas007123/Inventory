"""What an error e-mail may contain. Django already hides settings like passwords and keys; this also hides every submitted
form value and the cookies, so a crash while someone signs in can never mail a password or a session to anyone."""
from django.views.debug import SafeExceptionReporterFilter


class HidePrivateRequestData(SafeExceptionReporterFilter):
    def get_post_parameters(self, request):
        if request is None:
            return {}
        try:
            return {name: '********' for name in request.POST}
        except Exception:                       # e.g. a request body that could not be read
            return {}

    def get_safe_cookies(self, request):
        return {name: '********' for name in getattr(request, 'COOKIES', {})}
