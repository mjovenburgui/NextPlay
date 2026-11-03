"""Configuración de la app accounts (usuarios, perfiles y cuentas vinculadas): al arrancar Django conecta sus señales
(accounts/signals.py).
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        """Registra las señales de la app al arrancar Django (crean el perfil de cada usuario nuevo).

        Pantallas: ninguna (se ejecuta al arrancar Django y conecta las señales de accounts/signals.py).
        """
        from . import signals  # noqa: F401
