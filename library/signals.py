from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ReviewImage


@receiver(post_delete, sender=ReviewImage)
def delete_review_image_files(sender, instance, **kwargs):
    """Borra del disco la imagen subida y su miniatura al quitarla de una reseña o al borrar la reseña o la cuenta."""
    instance.delete_files()
