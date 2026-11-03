"""Configuración de la app library (biblioteca, reseñas y lista de deseos): al arrancar Django conecta sus señales
(library/signals.py).
"""

from django.apps import AppConfig


class LibraryConfig(AppConfig):
    name = 'library'

    def ready(self):
        """Registra las señales de la app al arrancar Django (borran del disco las imágenes de las reseñas que se quitan).

        Pantallas: ninguna (se ejecuta al arrancar Django y conecta las señales de library/signals.py).
        """
        from . import signals  # noqa: F401
