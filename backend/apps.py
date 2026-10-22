from django.apps import AppConfig


class BackendConfig(AppConfig):
    name = 'backend'

    def ready(self):
        from . import login_audit  # noqa: F401  (connects the sign-in signals)
