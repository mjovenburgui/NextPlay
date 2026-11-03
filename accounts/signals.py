"""Señales de las cuentas: crean el perfil de cada usuario nuevo (al registrarse o desde el Panel de administración) y
borran del disco las imágenes de un perfil que se elimina.
"""

from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, raw=False, **kwargs):
    """Crea el perfil de un usuario justo después de crearlo.

    No hace nada al guardar un usuario ya existente ni al cargar fixtures (raw=True).

    Pantallas: Registro (crea el perfil de la cuenta nueva), Panel de administración (también al crear un usuario
    allí).
    """
    if created and not raw:
        Profile.objects.get_or_create(user=instance)


@receiver(post_delete, sender=Profile)
def delete_profile_images(sender, instance, **kwargs):
    """Borra del disco la foto, la cabecera y el fondo de un perfil eliminado (p. ej. al borrar la cuenta).

    Pantallas: Panel de administración (al borrar un usuario o un perfil).
    """
    for image in (instance.avatar, instance.banner, instance.background):
        if image:
            image.delete(save=False)
