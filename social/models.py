from django.conf import settings
from django.db import models
from django.urls import reverse

from accounts.models import Notification


class FollowManager(models.Manager):
    def follow(self, follower, followed):
        """Hace que follower siga a followed y avisa a followed en la campana; devuelve True si no lo seguía ya.

        El aviso llega solo la primera vez: si follower deja de seguir a followed y vuelve, no se repite. Lleva a
        la página de follower o, si followed no puede verla (perfil privado), a la lista de seguidores de
        followed, donde sale sin enlace. No comprueba si followed se puede seguir (eso lo mira social.views.follow).
        """
        _, created = self.get_or_create(follower=follower, followed=followed)
        notified = Notification.objects.filter(user=followed, kind=Notification.Kind.FOLLOW, actor=follower)
        if created and not notified.exists():
            profile = follower.profile
            if profile.visible_to(followed):
                url = profile.get_absolute_url()
            else:
                url = reverse("social:followers", args=[followed.username])
            Notification.objects.create(
                user=followed, kind=Notification.Kind.FOLLOW, actor=follower,
                text=f"{profile.name} ha empezado a seguirte."[:300], url=url,
            )
        return created

    def unfollow(self, follower, followed):
        """Hace que follower deje de seguir a followed; devuelve True si lo seguía."""
        deleted, _ = self.filter(follower=follower, followed=followed).delete()
        return bool(deleted)


class Follow(models.Model):
    """Un usuario (follower) sigue a otro (followed) para ver lo que hace.

    Va en un solo sentido y sin solicitudes: followed no tiene que aceptar ni seguir de vuelta. No se puede
    seguir dos veces a la misma persona ni seguirse a uno mismo (también lo impide la base de datos). Los
    perfiles privados no se pueden seguir, pero sus dueños sí pueden seguir a otros.
    """

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following", verbose_name="seguidor"
    )
    followed = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers", verbose_name="seguido"
    )
    created_at = models.DateTimeField("desde", auto_now_add=True)

    objects = FollowManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["follower", "followed"], name="unique_follow"),
            models.CheckConstraint(condition=~models.Q(follower=models.F("followed")), name="no_self_follow"),
        ]
        verbose_name = "seguimiento"
        verbose_name_plural = "seguimientos"

    def __str__(self):
        """Texto en el admin: «seguidor → seguido»."""
        return f"{self.follower} → {self.followed}"
