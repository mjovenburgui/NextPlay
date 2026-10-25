from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification
from library.models import COMMENT_MAX_LENGTH, HOURS_SOURCES, MAX_RATING, AttachedImage, LibraryEntry

POST_MAX_LENGTH = 2000
# Imágenes que caben en una publicación.
POST_MAX_IMAGES = 4


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


class Post(models.Model):
    """Publicación de un jugador: lo que quiera contar, con hasta POST_MAX_IMAGES imágenes y, si quiere, sobre un juego.

    Sale en el feed de la portada y en la actividad de su perfil (social.activity.post_published) y tiene su propia
    página. La ve quien puede ver su perfil (Profile.visible_filter). El texto lleva el mismo formato sencillo que
    las reseñas (library.markup) y, si es un spoiler, el texto y las imágenes van tapados hasta que se pulsan.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    # Puede ir sin texto si lleva imágenes (lo comprueba social.forms.PostForm).
    text = models.TextField("texto", max_length=POST_MAX_LENGTH, blank=True)
    game = models.ForeignKey(
        "games.Game", on_delete=models.SET_NULL, null=True, blank=True, related_name="posts", verbose_name="juego"
    )
    has_spoilers = models.BooleanField("es un spoiler", default=False)
    created_at = models.DateTimeField("publicada", auto_now_add=True)
    # Sus denuncias se borran con ella.
    reports = GenericRelation("library.Report", related_query_name="post")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "publicación"
        verbose_name_plural = "publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario: comienzo del texto»."""
        return f"{self.user}: {self.text[:40] or '(solo imágenes)'}"

    def get_absolute_url(self):
        """URL de la página de la publicación: /u/<usuario>/publicaciones/<id>/."""
        return reverse("social:post", args=[self.user.username, self.pk])


class PostImage(AttachedImage):
    """Imagen de una publicación (como mucho POST_MAX_IMAGES). Se guardan en publicaciones/<id del autor>/."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="images")

    class Meta(AttachedImage.Meta):
        verbose_name = "imagen de una publicación"
        verbose_name_plural = "imágenes de las publicaciones"

    def __str__(self):
        """Texto en el admin: «publicación · imagen n»."""
        return f"{self.post} · imagen {self.position + 1}"

    def upload_folder(self):
        """Las imágenes de las publicaciones van en publicaciones/<id del autor>."""
        return f"publicaciones/{self.post.user_id}"


class PostLike(models.Model):
    """«Me gusta» de un usuario en una publicación (uno por usuario y publicación)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_likes")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="unique_like_per_user_post")]
        verbose_name = "me gusta de una publicación"
        verbose_name_plural = "me gusta de las publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario → publicación»."""
        return f"{self.user} → {self.post}"


class PostComment(models.Model):
    """Comentario en una publicación, como los de las reseñas. Lo pueden borrar quien lo escribió y el autor de la publicación."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_comments")
    text = models.TextField("comentario", max_length=COMMENT_MAX_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)
    # Sus denuncias se borran con él.
    reports = GenericRelation("library.Report", related_query_name="post_comment")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "comentario de una publicación"
        verbose_name_plural = "comentarios de las publicaciones"

    def __str__(self):
        """Texto en el admin: «usuario en publicación: comienzo del comentario»."""
        return f"{self.user} en {self.post}: {self.text[:40]}"

    def get_absolute_url(self):
        """URL del comentario dentro de la página de su publicación."""
        return f"{self.post.get_absolute_url()}#comentario-{self.pk}"

    def can_delete(self, user):
        """Indica si user puede borrar el comentario: es suyo o es el autor de la publicación."""
        return user.is_authenticated and user.pk in (self.user_id, self.post.user_id)


class Activity(models.Model):
    """Algo que ha hecho un jugador: sale en la pestaña Actividad de su perfil y en el feed de la portada.

    La apunta social.activity desde lo que hace el jugador, nunca con señales: las importaciones también
    guardan juegos y les cambian el estado, y eso no lo ha hecho él. Hay tres tipos:

    - GAME: lo que hace con un juego de su biblioteca. Lo que hace con el mismo juego en poco rato se junta en
      una sola (ver social.activity.MERGE_WINDOW): el último estado al que lo ha pasado (status), la nota que le
      ha puesto (rating) y la reseña que ha publicado (review), las tres o solo alguna.
    - IMPORT: los juegos nuevos de una importación de Steam, PlayStation o Xbox, todos juntos. En data van la
      plataforma («platform», clave de HOURS_SOURCES), cuántos son («count») y los ids de los más jugados
      («games»), para enseñar sus portadas.
    - POST: una publicación (post), con su juego si lo tiene. Se borra con ella.

    updated_at es la hora de lo último que se ha juntado: la que se enseña y por la que se ordena.
    """

    class Kind(models.TextChoices):
        GAME = "game", "Juego de la biblioteca"
        IMPORT = "import", "Importación"
        POST = "post", "Publicación"

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
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, null=True, blank=True, related_name="+", verbose_name="publicación"
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
    def has_content(self):
        """Indica si hay algo que enseñar de ella a quien la mira: los juegos de una importación, la publicación o,
        en las de un juego, el estado, la nota o la reseña (si su reseña ya no se puede leer, puede que nada)."""
        if self.kind == self.Kind.IMPORT:
            return bool(self.import_count)
        if self.kind == self.Kind.POST:
            return self.post is not None
        return bool(self.status or self.rating or self.review)

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
        """Lo que hizo, en una línea y sin formato, para el admin: «terminó Hades», «importó 12 juegos de Steam» o «publicó»."""
        if self.kind == self.Kind.IMPORT:
            return f"importó {self.import_count} juegos de {self.platform_name}"
        if self.kind == self.Kind.POST:
            return f"publicó sobre {self.game}" if self.game_id else "publicó"
        before, after = self.verb
        return " ".join(part for part in (before, str(self.game), after) if part)
