from django.apps import AppConfig


class SocialConfig(AppConfig):
    name = "social"

    def ready(self):
        """Registra las señales de la app al arrancar Django (borran la actividad que se queda vacía al borrar una reseña)."""
        from . import signals  # noqa: F401
