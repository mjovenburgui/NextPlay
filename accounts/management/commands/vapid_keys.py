"""Comando «manage.py vapid_keys»: genera las claves VAPID de los avisos push (accounts.vapid) y escribe las dos líneas
que hay que añadir al archivo .env. No guarda nada.
"""

from django.core.management.base import BaseCommand

from accounts import vapid


class Command(BaseCommand):
    help = (
        "Genera un par de claves VAPID para los avisos push y escribe las líneas VAPID_PUBLIC_KEY y VAPID_PRIVATE_KEY "
        "que hay que añadir al archivo .env. Si se cambian, cada dispositivo tendrá que volver a activar los avisos."
    )

    def handle(self, *args, **options):
        """Escribe las dos líneas de .env con un par de claves nuevo y cómo usarlas.

        Pantallas: Editar perfil (Avisos en el móvil y el ordenador: sin estas claves no se pueden activar).
        """
        public, private = vapid.generate_keys()
        self.stdout.write(f"VAPID_PUBLIC_KEY={public}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={private}")
        self.stderr.write(
            "Añade estas dos líneas al archivo .env y reinicia el servidor. La privada no se enseña a nadie: si las "
            "cambias, cada dispositivo tendrá que volver a activar los avisos."
        )
