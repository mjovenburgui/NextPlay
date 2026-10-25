from django.db.models.signals import post_delete
from django.dispatch import receiver

from library.models import Review

from .models import Activity


@receiver(post_delete, sender=Review)
def forget_empty_activity(sender, instance, **kwargs):
    """Al borrar una reseña, borra la actividad que solo era esa reseña; la que tenía también estado o nota se queda sin ella."""
    Activity.objects.filter(
        user_id=instance.user_id, kind=Activity.Kind.GAME, game_id=instance.game_id,
        review__isnull=True, status="", rating__isnull=True,
    ).delete()
