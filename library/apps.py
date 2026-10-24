from django.apps import AppConfig


class LibraryConfig(AppConfig):
    name = 'library'

    def ready(self):
        """Registra las señales de la app al arrancar Django (borran del disco las imágenes de las reseñas que se quitan)."""
        from . import signals  # noqa: F401
