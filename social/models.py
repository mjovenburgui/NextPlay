from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification
from library.models import HOURS_SOURCES, MAX_RATING, LibraryEntry


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


class Activity(models.Model):
    """Algo que ha hecho un jugador: sale en la pestaña Actividad de su perfil y, más adelante, en el feed de sus seguidores.

    La apunta social.activity desde lo que hace el jugador, nunca con señales: las importaciones también
    guardan juegos y les cambian el estado, y eso no lo ha hecho él. Hay dos tipos:

    - GAME: lo que hace con un juego de su biblioteca. Lo que hace con el mismo juego en poco rato se junta en
      una sola (ver social.activity.MERGE_WINDOW): el último estado al que lo ha pasado (status), la nota que le
      ha puesto (rating) y la reseña que ha publicado (review), las tres o solo alguna.
    - IMPORT: los juegos nuevos de una importación de Steam, PlayStation o Xbox, todos juntos. En data van la
      plataforma («platform», clave de HOURS_SOURCES), cuántos son («count») y los ids de los más jugados
      («games»), para enseñar sus portadas.

    updated_at es la hora de lo último que se ha juntado: la que se enseña y por la que se ordena.
    """

    class Kind(models.TextChoices):
        GAME = "game", "Juego de la biblioteca"
        IMPORT = "import", "Importación"

    # Estado al que pasa el juego → lo que dice la actividad, en minúscula y partido en lo que va antes del
    # nombre del juego y lo que va después: «terminó Hades», «añadió Hades a Pendiente».
    STATUS_VERBS = {
        LibraryEntry.Status.PLAYING: ("empezó a jugar a", ""),
        LibraryEntry.Status.COMPLETED: ("terminó", ""),
        LibraryEntry.Status.PLAYED: ("jugó a", ""),
        LibraryEntry.Status.BACKLOG: ("añadió", "a Pendiente"),
        LibraryEntry.Status.DROPPED: ("abandonó", ""),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    game = models.ForeignKey(
        "games.Game", on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="juego"
    )
    status = models.CharField("estado", max_length=20, choices=LibraryEntry.Status.choices, blank=True)
    rating = models.PositiveSmallIntegerField(
        "nota", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(MAX_RATING)]
    )
    # Si se borra la reseña se queda lo demás; si ya no queda nada, social.signals borra la actividad.
    review = models.ForeignKey(
        "library.Review", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="reseña"
    )
    data = models.JSONField("datos", default=dict, blank=True)
    created_at = models.DateTimeField("primera acción", default=timezone.now)
    updated_at = models.DateTimeField("última acción", default=timezone.now)

    class Meta:
        ordering = ["-updated_at", "-id"]
        # La actividad de cada uno (su pestaña y Siguiendo) y la de todos (Descubrir), de lo más nuevo a lo más viejo.
        indexes = [
            models.Index(fields=["user", "-updated_at", "-id"], name="activity_user_recent"),
            models.Index(fields=["-updated_at", "-id"], name="activity_recent"),
        ]
        verbose_name = "actividad"
        verbose_name_plural = "actividad"

    def __str__(self):
        """Texto en el admin: «usuario · lo que hizo»."""
        return f"{self.user} · {self.summary}"

    @property
    def is_empty(self):
        """Indica si una actividad de un juego se ha quedado sin nada (sin estado, nota ni reseña) y sobra."""
        return self.kind == self.Kind.GAME and not self.status and self.rating is None and self.review_id is None

    @property
    def verb(self):
        """Qué hizo con el juego, en minúscula: (lo que va antes del nombre del juego, lo que va después).

        Con un estado, lo de STATUS_VERBS; si no, «reseñó» si publicó una reseña que se puede leer, o «valoró».
        """
        if self.status in self.STATUS_VERBS:
            return self.STATUS_VERBS[self.status]
        return ("reseñó", "") if self.review else ("valoró", "")

    @property
    def shown_rating(self):
        """Nota que se enseña: la que le puso o, si no, la de su reseña (si la reseña se puede leer); o None."""
        if self.rating:
            return self.rating
        review = self.review
        return review.rating if review else None

    @property
    def platform_name(self):
        """Nombre de la plataforma de una importación («Steam», «PlayStation» o «Xbox»)."""
        return HOURS_SOURCES.get(self.data.get("platform"), "")

    @property
    def import_count(self):
        """Cuántos juegos nuevos trajo una importación."""
        return self.data.get("count", 0)

    @property
    def summary(self):
        """Lo que hizo, en una línea y sin formato, para el admin: «terminó Hades» o «importó 12 juegos de Steam»."""
        if self.kind == self.Kind.IMPORT:
            return f"importó {self.import_count} juegos de {self.platform_name}"
        before, after = self.verb
        return " ".join(part for part in (before, str(self.game), after) if part)
