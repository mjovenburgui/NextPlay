"""Configuración de la app social (seguir, publicaciones y actividad): al arrancar Django conecta sus señales
(social/signals.py).
"""

from django.apps import AppConfig


class SocialConfig(AppConfig):
    name = "social"

    def ready(self):
        """Registra las señales de la app al arrancar Django: borran la actividad que se queda vacía al borrar una reseña
        y los archivos de las imágenes de las publicaciones que se borran.

        Pantallas: ninguna (se ejecuta al arrancar Django y conecta las señales de social/signals.py).
        """
        from . import signals  # noqa: F401
