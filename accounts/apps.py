from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        """Registra las señales de la app al arrancar Django (crean el perfil de cada usuario nuevo)."""
        from . import signals  # noqa: F401
